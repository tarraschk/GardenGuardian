#!/usr/bin/env python3
"""Suivi d'oiseaux avec la camera sur pan/tilt, detection sur le Hailo-8.

Variante de hand_track_hailo.py : au lieu du modele de POSE (poignet), on
utilise le detecteur d'objets COCO yolov8s (80 classes, NMS embarque sur la
puce). On filtre la classe voulue (par defaut "bird") et on vise le CENTRE de
la bounding box de la cible la plus confiante. Plus de cible -> retour repos.

Quand la cible est VISEE (centree dans le cadre, sous AIM_TOL) et assez
confiante, on declenche le pistolet a eau via le relais (watergun.py) : rafale
courte non bloquante (BURST_S) puis cooldown (COOLDOWN_S) pour ne pas vider le
reservoir. Le tir est desactive en --dry-run et avec --no-fire.

A chaque tir, un court clip (pre-tir + jet) est encode (cv2/H264) et envoye
sur Telegram (telegram_notify.py), dans un thread -> jamais dans le callback
GStreamer. Necessite des identifiants Telegram (cf. telegram_notify.py) ;
desactivable avec --no-telegram.

Pipeline GStreamer autonome (memes briques que le pose, aucun clone/venv) :
  libcamerasrc -> mise a l'echelle 640x640 RGB -> hailonet (.hef detection)
              -> hailofilter (libyolo_hailortpp_post.so, fn filter) -> appsink

Usage :
  # prototypage interieur : pas de pigeon dans le salon -> teste-toi en "person"
  python3 track_bird_hailo.py --classe person --dry-run   # detection seule, ne tire pas
  python3 track_bird_hailo.py --classe person --no-fire   # suit mais ne tire pas
  python3 track_bird_hailo.py --classe person             # suit ET tire (test interieur)
  python3 track_bird_hailo.py --classe person --cooldown 10  # tire avec 10s entre rafales (anti-arrosage en test)
  # deploiement balcon :
  python3 track_bird_hailo.py                              # suit "bird" (defaut) et tire
Ctrl+C -> retour repos + relache + pistolet coupe.
"""
import argparse
import os
import sys
import tempfile
import threading
import time
from collections import deque

import cv2
import numpy as np

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib
import hailo

import servo_config as cfg
from servo_driver import ServoController
import telegram_notify

HEF = "/usr/share/hailo-models/yolov8s_h8.hef"        # detecteur COCO, Hailo-8 plein
SO = "/usr/lib/aarch64-linux-gnu/hailo/tappas/post_processes/libyolo_hailortpp_post.so"
FN = "filter"                                         # decode la sortie NMS du HEF
NET = 640                                             # taille d'entree reseau

# --- Reglages asservissement (memes principes que le suivi de main) ---
DEADZONE = 0.06          # |erreur normalisee| en deca: on ne bouge pas
KP_PAN = 220.0           # gain pan (us / unite d'erreur 0..0.5)
KP_TILT = 180.0          # gain tilt
MAX_STEP_US = 35         # deplacement max par frame (lissage)
PAN_SIGN = -1            # corrige 2026-06-01 : cible a gauche -> camera tourne a gauche (valide live)
TILT_SIGN = +1           # valide live 2026-06-01 : cible en haut -> camera monte. Coherent avec la mesure
                         #   servo (us↑=descend) + image droite. Un detour par -1 a confirme que +1 est bon.
NO_TARGET_TIMEOUT = 2.0  # s sans cible -> retour repos (tolere les trous de detection)
RETURN_STEP_US = 20      # vitesse de retour repos (us/frame)
MIN_DET_CONF = 0.25      # confiance mini (le NMS de la puce coupe deja a 0.20 -> plancher)
SMOOTH_ALPHA = 0.5       # lissage cible: 1.0 = brut/nerveux, ->0 = tres lisse/lent
REST_TOL_US = 8          # consideree "au repos" si a moins de ca de la consigne
RELEASE_AFTER = 1.5      # s d'inactivite (apres retour repos) avant de couper le PWM

# --- Tir (pistolet a eau via relais, cf. watergun.py) ---
AIM_TOL = 0.10           # |erreur normalisee| sous laquelle la cible est "visee" -> tir autorise
FIRE_MIN_CONF = 0.40     # confiance mini pour tirer (> MIN_DET_CONF : on n'arrose pas un doute)
BURST_S = 0.4            # duree d'une rafale
COOLDOWN_S = 3.0         # delai mini entre 2 rafales (anti-vidange reservoir)

# --- Clip video envoye sur Telegram a chaque tir (cf. telegram_notify.py) ---
CLIP_PRE_S = 1.0         # secondes de pre-tir gardees (montre la cible visee)
CLIP_POST_S = 1.5        # secondes filmees apres le debut du tir (montre le jet)
NOTIFY_COOLDOWN_S = 20.0 # delai mini entre 2 envois Telegram (anti-spam)
RING_FPS = 35            # majorant de fps pour dimensionner le tampon pre-tir


def build_pipeline():
    return (
        f"libcamerasrc ! "
        f"video/x-raw,format=NV12,width=1280,height=720 ! "
        f"queue max-size-buffers=3 leaky=downstream ! "
        f"videoconvert n-threads=2 ! videoscale n-threads=2 ! "
        f"video/x-raw,format=RGB,width={NET},height={NET} ! "
        f"queue max-size-buffers=3 leaky=downstream ! "
        f"hailonet hef-path={HEF} batch-size=1 ! "
        f"queue max-size-buffers=3 leaky=downstream ! "
        f"hailofilter so-path={SO} function-name={FN} qos=false ! "
        f"queue max-size-buffers=3 leaky=downstream ! "
        f"appsink name=sink emit-signals=true sync=false max-buffers=1 drop=true"
    )


class Tracker:
    def __init__(self, target_label, dry_run, fire, telegram, cooldown_s=COOLDOWN_S):
        self.target_label = target_label
        self.dry_run = dry_run
        self.cooldown_s = cooldown_s   # delai mini entre 2 rafales (surchargeable via --cooldown)
        self.servos = None if dry_run else ServoController()
        if self.servos:
            self.servos.go_rest()
        self.last_seen = time.monotonic()
        self.released = False    # True quand le PWM est coupe (servos mous au repos)
        self.smooth = None       # position cible lissee (cx, cy) ou None si pas de lock
        self.frames = 0
        self.t0 = time.monotonic()
        # --- tir : actif seulement si servos pilotes (on vise) et --no-fire absent ---
        self.fire_enabled = fire and not dry_run
        self.wg = None
        if self.fire_enabled:
            import watergun            # import tardif : ne reserve GPIO17 que si on tire
            self.wg = watergun
        self.firing = False           # rafale en cours
        self.fire_until = 0.0         # instant de fin de la rafale courante
        self.cooldown_until = 0.0     # avant cet instant, pas de nouvelle rafale
        self.shots = 0
        # --- clip Telegram : seulement si on tire ET identifiants presents ---
        self.tg_enabled = telegram and self.fire_enabled and telegram_notify.enabled()
        self.ring = deque(maxlen=int(CLIP_PRE_S * RING_FPS) + 1)  # frames pre-tir
        self.recording = False        # True pendant la capture d'un clip
        self.clip_frames = []         # frames du clip en cours (pre + post)
        self.clip_end = 0.0           # instant de fin de capture du clip
        self.notify_until = 0.0       # avant cet instant, pas de nouvel envoi
        self._clip_conf = 0.0
        self._clip_shot = 0

    def pick_target(self, dets):
        """Detection la plus confiante de la classe voulue, ou None.
        Renvoie (centre_x, centre_y) normalises plein cadre via la bbox."""
        best = None
        for d in dets:
            if d.get_confidence() < MIN_DET_CONF:
                continue
            if d.get_label() != self.target_label:
                continue
            if best is None or d.get_confidence() > best.get_confidence():
                best = d
        if best is None:
            return None
        bb = best.get_bbox()
        cx = bb.xmin() + bb.width() / 2.0
        cy = bb.ymin() + bb.height() / 2.0
        return cx, cy, best.get_confidence()

    def update_fire(self, now, aimed, conf):
        """Machine a etats du tir, non bloquante (appelee a chaque frame).
        Coupe la rafale en cours a son terme ; en demarre une si la cible est
        visee, assez confiante, et hors cooldown."""
        if not self.fire_enabled:
            return
        # fin de la rafale en cours (quoi qu'il arrive, meme si la cible a disparu)
        if self.firing and now >= self.fire_until:
            self.wg.off()
            self.firing = False
        # nouvelle rafale : cible visee + confiante + cooldown ecoule
        if (aimed and conf >= FIRE_MIN_CONF
                and not self.firing and now >= self.cooldown_until):
            self.wg.on()
            self.firing = True
            self.fire_until = now + BURST_S
            self.cooldown_until = now + BURST_S + self.cooldown_s
            self.shots += 1
            self._start_clip(now, conf)   # filme le tir pour Telegram

    def _extract_frame(self, buf):
        """Copie RGB (NET x NET x 3) du buffer GStreamer, ou None si echec."""
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            return None
        try:
            n = NET * NET * 3
            arr = np.frombuffer(info.data, dtype=np.uint8, count=n)
            return arr.reshape((NET, NET, 3)).copy()
        except (ValueError, TypeError):
            return None
        finally:
            buf.unmap(info)

    def _capture_frame(self, now, buf):
        """Alimente le tampon pre-tir, ou le clip en cours d'enregistrement."""
        frame = self._extract_frame(buf)
        if frame is None:
            return
        if self.recording:
            self.clip_frames.append((now, frame))
            if now >= self.clip_end:
                self._finish_clip()
        else:
            self.ring.append((now, frame))

    def _start_clip(self, now, conf):
        """Demarre la capture d'un clip : pre-tir (tampon) + post-tir a venir."""
        if not self.tg_enabled or self.recording or now < self.notify_until:
            return
        self.recording = True
        self.clip_frames = list(self.ring)        # pre-roll deja en memoire
        self.clip_end = now + CLIP_POST_S
        self.notify_until = now + NOTIFY_COOLDOWN_S
        self._clip_conf = conf
        self._clip_shot = self.shots

    def _finish_clip(self):
        """Cloture le clip et lance encodage + envoi dans un thread."""
        frames = self.clip_frames
        self.clip_frames = []
        self.recording = False
        self.ring.clear()
        threading.Thread(target=self._encode_and_send,
                         args=(frames, self._clip_conf, self._clip_shot),
                         daemon=True).start()

    def _encode_and_send(self, frames, conf, shot):
        """Encode les frames en mp4/H264 et l'envoie sur Telegram (thread)."""
        if len(frames) < 2:
            return
        ts = [t for t, _ in frames]
        span = ts[-1] - ts[0]
        fps = (len(frames) - 1) / span if span > 0 else 30.0
        fps = max(5.0, min(60.0, fps))
        fd, path = tempfile.mkstemp(prefix="garden_", suffix=".mp4")
        os.close(fd)
        vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"avc1"),
                             fps, (NET, NET))
        if not vw.isOpened():
            print("\n[clip] VideoWriter KO (codec avc1 indispo).", file=sys.stderr)
            return
        for _, f in frames:
            vw.write(cv2.cvtColor(f, cv2.COLOR_RGB2BGR))
        vw.release()
        caption = (f"GardenGuardian \U0001f6bf tir #{shot} sur "
                   f"{self.target_label} (conf {conf:.0%}) "
                   f"{time.strftime('%H:%M:%S')}")
        telegram_notify.post_video(path, caption)
        try:
            os.remove(path)
        except OSError:
            pass

    def on_sample(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        buf = sample.get_buffer()
        roi = hailo.get_roi_from_buffer(buf)
        dets = roi.get_objects_typed(hailo.HAILO_DETECTION)
        now = time.monotonic()

        if self.tg_enabled:
            self._capture_frame(now, buf)

        target = self.pick_target(dets)

        if target is not None:
            cx, cy, conf = target
            self.last_seen = now
            # lissage exponentiel pour amortir une detection nerveuse
            if self.smooth is None:
                self.smooth = (cx, cy)
            else:
                a = SMOOTH_ALPHA
                self.smooth = (a * cx + (1 - a) * self.smooth[0],
                               a * cy + (1 - a) * self.smooth[1])
            sx, sy = self.smooth
            ex, ey = sx - 0.5, sy - 0.5
            if not self.dry_run:
                if self.released:
                    # reactive le PWM a la derniere position connue avant de bouger
                    for ch in cfg.SERVO:
                        self.servos.set_us(ch, self.servos.pos_us[ch])
                    self.released = False
                if abs(ex) > DEADZONE:
                    d = PAN_SIGN * KP_PAN * ex
                    self.servos.nudge(cfg.PAN, int(max(-MAX_STEP_US, min(MAX_STEP_US, d))))
                if abs(ey) > DEADZONE:
                    d = TILT_SIGN * KP_TILT * ey
                    self.servos.nudge(cfg.TILT, int(max(-MAX_STEP_US, min(MAX_STEP_US, d))))
            # cible visee = centree dans le cadre sur les 2 axes
            aimed = abs(ex) < AIM_TOL and abs(ey) < AIM_TOL
            self.update_fire(now, aimed, conf)
            tag = "TIR" if self.firing else ("vise" if aimed else "suit")
            self._status(f"{self.target_label} x={cx:.2f} y={cy:.2f} c={conf:.2f} [{tag}]")
        else:
            self.update_fire(now, aimed=False, conf=0.0)
            # cible perdue depuis assez longtemps -> oublie le lissage (re-acquisition propre)
            if (now - self.last_seen) > NO_TARGET_TIMEOUT:
                self.smooth = None
            # pas de cible -> retour progressif au repos apres timeout, puis coupure PWM
            if not self.dry_run and not self.released and (now - self.last_seen) > NO_TARGET_TIMEOUT:
                at_rest = True
                for ch in cfg.SERVO:
                    delta = cfg.SERVO[ch]["rest_us"] - self.servos.pos_us[ch]
                    if abs(delta) > REST_TOL_US:
                        at_rest = False
                    step = int(max(-RETURN_STEP_US, min(RETURN_STEP_US, delta)))
                    if step:
                        self.servos.nudge(ch, step)
                # une fois au repos depuis assez longtemps -> on relache (servos mous)
                if at_rest and (now - self.last_seen) > NO_TARGET_TIMEOUT + RELEASE_AFTER:
                    self.servos.release_all()
                    self.released = True
            self._status("relache (mou)" if self.released else f"aucun {self.target_label}")
        return Gst.FlowReturn.OK

    def _status(self, msg):
        self.frames += 1
        dt = time.monotonic() - self.t0
        fps = self.frames / dt if dt > 0 else 0
        if self.servos:
            pos = f"pan={self.servos.pos_us[cfg.PAN]:4d} tilt={self.servos.pos_us[cfg.TILT]:4d}"
        else:
            pos = "[dry-run]"
        shots = f" | tirs={self.shots}" if self.fire_enabled else ""
        print(f"\r{fps:4.1f}fps | {msg:40s} | {pos}{shots}   ", end="", flush=True)

    def shutdown(self):
        if self.wg:                       # coupe le pistolet en priorite
            self.wg.off()
            self.firing = False
        if self.servos:
            self.servos.go_rest()
            time.sleep(0.3)
            self.servos.release_all()
            self.servos.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--classe", default="bird",
                    help="label COCO a suivre (def: bird ; 'person' pour te tester en interieur)")
    ap.add_argument("--dry-run", action="store_true",
                    help="detection seule, ne bouge PAS les servos et ne tire PAS")
    ap.add_argument("--no-fire", action="store_true",
                    help="suit la cible mais ne declenche PAS le pistolet")
    ap.add_argument("--no-telegram", action="store_true",
                    help="n'envoie PAS de clip video sur Telegram")
    ap.add_argument("--cooldown", type=float, default=COOLDOWN_S,
                    help=f"delai mini entre 2 rafales en s (def: {COOLDOWN_S:g} ; "
                         "ex: 10 pour eviter de se faire arroser en rafale pendant les tests)")
    args = ap.parse_args()

    want_tg = not args.no_telegram and not args.dry_run and not args.no_fire
    if want_tg and not telegram_notify.enabled():
        print("[telegram] identifiants absents -> clips desactives "
              "(cf. telegram_notify.py). Lance 'python3 telegram_notify.py test'.",
              file=sys.stderr)

    Gst.init(None)
    pipeline = Gst.parse_launch(build_pipeline())
    tracker = Tracker(args.classe, args.dry_run, fire=not args.no_fire,
                      telegram=not args.no_telegram, cooldown_s=args.cooldown)
    sink = pipeline.get_by_name("sink")
    sink.connect("new-sample", tracker.on_sample)

    loop = GLib.MainLoop()
    bus = pipeline.get_bus()
    bus.add_signal_watch()

    def on_msg(_bus, message):
        t = message.type
        if t == Gst.MessageType.EOS:
            loop.quit()
        elif t == Gst.MessageType.ERROR:
            err, dbg = message.parse_error()
            print(f"\n[GST ERROR] {err}: {dbg}", file=sys.stderr)
            loop.quit()
    bus.connect("message", on_msg)

    pipeline.set_state(Gst.State.PLAYING)
    if args.dry_run:
        mode = "DRY-RUN (servos NON pilotes, ne tire pas)"
    elif args.no_fire:
        mode = "tracking ACTIF (ne tire pas)"
    else:
        mode = f"tracking ACTIF + TIR arme (cooldown {args.cooldown:g}s)"
    if tracker.tg_enabled:
        mode += " + clip Telegram"
    print(f"Demarre en mode {mode}, cible='{args.classe}'. Ctrl+C pour arreter.")
    try:
        loop.run()
    except KeyboardInterrupt:
        print("\nArret demande.")
    finally:
        pipeline.set_state(Gst.State.NULL)
        tracker.shutdown()
        print("Termine (servos au repos puis relaches).")


if __name__ == "__main__":
    main()
