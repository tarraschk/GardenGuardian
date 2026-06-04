#!/usr/bin/env python3
"""Pilotage du pistolet a eau via le relais AA024 (couplage optique) sur GPIO17.

Cablage (verifie 2026-06-01) :
    VCC  relais -> pin 2  (5V)
    GND  relais -> pin 6  (GND)
    IN   relais -> pin 11 (GPIO17)
    Contacts NO du relais en parallele sur la gachette du pistolet.

Le relais AA024 est a couplage optique et a declenchement ACTIF-BAS
(verifie 2026-06-01) : sa ligne IN est tiree a HIGH au repos par la carte,
relais ouvert = pompe OFF. C'est LOW qui colle le relais = gachette "appuyee"
= la pompe tourne (sur sa propre batterie).

-> RELAY_ACTIVE_HIGH = False. gpiozero gere l'inversion : on() force LOW
(tir), off() force HIGH (repos). initial_value=False -> pin force HIGH des
l'init = aucun tir au demarrage. A la sortie du process, gpiozero relache le
pin en entree ; le pull-up de la carte le maintient HIGH = pompe OFF.

Si un module au cablage different (actif-haut) est utilise, repasser
RELAY_ACTIVE_HIGH a True.

Securite :
    - rafale bornee a MAX_BURST_S (on ne vide pas le reservoir par erreur) ;
    - le relais est TOUJOURS relache en sortie (finally + atexit) -> jamais
      de pompe bloquee en marche apres un Ctrl+C ou un plantage.

Usage :
    python3 watergun.py test          # impulsion courte (0.3 s) -> verif cablage
    python3 watergun.py fire [duree]  # rafale de [duree] s (defaut 0.5, max 3)
    python3 watergun.py on             # colle le relais (DANGER : tire en continu)
    python3 watergun.py off            # relache le relais (= arret d'urgence)
"""
import atexit
import sys
import time

from gpiozero import OutputDevice

RELAY_GPIO = 17
RELAY_ACTIVE_HIGH = False  # relais actif-bas : GPIO LOW = pompe ON (cf. docstring)
MAX_BURST_S = 3.0          # garde-fou : aucune rafale ne depasse ca
DEFAULT_BURST_S = 0.5
TEST_BURST_S = 0.3

# initial_value=False -> relais relache des l'init (pas de tir au demarrage).
_relay = OutputDevice(RELAY_GPIO, active_high=RELAY_ACTIVE_HIGH, initial_value=False)


def off():
    """Relache le relais -> pompe coupee. C'est aussi l'arret d'urgence."""
    _relay.off()


def on():
    """Colle le relais -> pompe en marche, en continu, jusqu'a off()."""
    _relay.on()


def fire(duration_s):
    """Tire pendant duration_s (bornee a [0, MAX_BURST_S]), puis coupe."""
    d = max(0.0, min(float(duration_s), MAX_BURST_S))
    if d != float(duration_s):
        print(f"Duree {duration_s}s bornee a {d}s (max {MAX_BURST_S}s).")
    try:
        on()
        time.sleep(d)
    finally:
        off()


# Filet de securite : quoi qu'il arrive, on relache le relais en quittant.
atexit.register(off)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "test"

    if cmd == "off":
        off()
        print("Relais relache - pompe coupee.")
    elif cmd == "on":
        print("Relais colle - pompe EN MARCHE. Ctrl+C ou 'off' pour couper.")
        on()
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("\nInterrompu.")
        finally:
            off()
            print("Relais relache - pompe coupee.")
    elif cmd in ("test", "fire"):
        d = TEST_BURST_S if cmd == "test" else (
            float(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_BURST_S)
        print(f"Tir de {min(d, MAX_BURST_S)}s sur GPIO{RELAY_GPIO}... "
              f"(pistolet pointe quelque part de sur ?)")
        fire(d)
        print("Tir termine - relais relache.")
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
