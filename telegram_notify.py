#!/usr/bin/env python3
"""Envoi de notifications Telegram (video / message) pour GardenGuardian.

Identifiants lus dans cet ordre :
  1. variables d'environnement TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
  2. fichier telegram_config.py a cote (BOT_TOKEN = "..." ; CHAT_ID = "...")

Mise en place (une fois) :
  - Cree un bot via @BotFather sur Telegram -> recupere le TOKEN.
  - Envoie un message a ton bot, puis recupere ton CHAT_ID :
        python3 telegram_notify.py chatid
  - Copie telegram_config.example.py -> telegram_config.py et remplis-le,
    ou exporte les 2 variables d'environnement.
  - Teste :  python3 telegram_notify.py test

API :
  enabled()                 -> True si les identifiants sont presents
  post_video(path, caption) -> envoi BLOQUANT (a appeler depuis un thread a toi)
  send_video(path, caption) -> envoi non bloquant (thread interne)
  send_message(text)        -> envoi non bloquant d'un texte
"""
import os
import sys
import threading

import requests

API = "https://api.telegram.org/bot{token}/{method}"


def _creds():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        try:
            import telegram_config as c
            token = token or getattr(c, "BOT_TOKEN", None)
            chat = chat or getattr(c, "CHAT_ID", None)
        except ImportError:
            pass
    return token, (str(chat) if chat else None)


def enabled():
    token, chat = _creds()
    return bool(token and chat)


def post_video(path, caption=""):
    """Envoie la video (bloquant). Renvoie True si OK. N'explose jamais :
    les erreurs reseau sont juste loguees (on ne veut pas planter le tracker)."""
    token, chat = _creds()
    if not (token and chat):
        print("[telegram] identifiants absents, envoi ignore.", file=sys.stderr)
        return False
    url = API.format(token=token, method="sendVideo")
    try:
        with open(path, "rb") as f:
            r = requests.post(
                url,
                data={"chat_id": chat, "caption": caption,
                      "supports_streaming": True},
                files={"video": ("garden.mp4", f, "video/mp4")},
                timeout=60,
            )
        if r.status_code != 200:
            print(f"[telegram] sendVideo HTTP {r.status_code}: {r.text[:200]}",
                  file=sys.stderr)
            return False
        return True
    except Exception as e:                      # reseau coupe, DNS, etc.
        print(f"[telegram] echec envoi video: {e}", file=sys.stderr)
        return False


def send_video(path, caption=""):
    """Variante non bloquante (thread interne)."""
    threading.Thread(target=post_video, args=(path, caption), daemon=True).start()


def post_message(text):
    token, chat = _creds()
    if not (token and chat):
        print("[telegram] identifiants absents, envoi ignore.", file=sys.stderr)
        return False
    url = API.format(token=token, method="sendMessage")
    try:
        r = requests.post(url, data={"chat_id": chat, "text": text}, timeout=20)
        return r.status_code == 200
    except Exception as e:
        print(f"[telegram] echec envoi message: {e}", file=sys.stderr)
        return False


def send_message(text):
    threading.Thread(target=post_message, args=(text,), daemon=True).start()


def _print_chat_id():
    """Affiche le(s) chat_id vus dans les derniers messages recus par le bot.
    Envoie d'abord un message a ton bot, puis lance cette commande."""
    token, _ = _creds()
    if not token:
        print("TELEGRAM_BOT_TOKEN absent (env ou telegram_config.py).")
        return
    r = requests.get(API.format(token=token, method="getUpdates"), timeout=20)
    data = r.json()
    seen = {}
    for upd in data.get("result", []):
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat", {})
        if "id" in chat:
            seen[chat["id"]] = chat.get("title") or chat.get("username") or \
                chat.get("first_name", "")
    if not seen:
        print("Aucun message recu. Envoie d'abord un message a ton bot, puis "
              "relance 'python3 telegram_notify.py chatid'.")
    else:
        print("CHAT_ID detecte(s) :")
        for cid, name in seen.items():
            print(f"  {cid}   ({name})")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "test"
    if cmd == "chatid":
        _print_chat_id()
    elif cmd == "test":
        if not enabled():
            print("Identifiants absents : exporte TELEGRAM_BOT_TOKEN/"
                  "TELEGRAM_CHAT_ID ou cree telegram_config.py.")
            sys.exit(1)
        ok = post_message("GardenGuardian : test Telegram OK ✅")
        print("Message envoye." if ok else "Echec (voir erreur ci-dessus).")
    else:
        print(__doc__)
        sys.exit(1)
