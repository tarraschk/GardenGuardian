#!/usr/bin/env python3
"""Place UN servo du Pan-Tilt HAT a une consigne donnee, puis coupe le signal.

Usage :
    python3 set_servo.py <canal> <microsecondes> [duree_s]
    python3 set_servo.py <canal> rest            # va a la position de repos

Exemples :
    python3 set_servo.py 0 850        # pan au repos, tenu 1 s puis signal coupe
    python3 set_servo.py 1 2000 2     # tilt a 2.0 ms, tenu 2 s puis coupe
    python3 set_servo.py 1 rest       # tilt a sa position de repos

Le signal est TOUJOURS coupe a la fin -> le servo ne reste jamais a forcer.
Ctrl+C interrompt et coupe aussi le signal.
Les consignes sont bornees a la plage sure de chaque axe (voir servo_config.py).
"""
import sys
import time

from smbus2 import SMBus

import servo_config as cfg

MODE1, PRESCALE = 0x00, 0xFE
LED0_ON_L = 0x06
RESTART, SLEEP, AI = 0x80, 0x10, 0x20
ADDR = cfg.PCA9685_ADDR


def init(bus):
    bus.write_byte_data(ADDR, MODE1, 0x00)
    time.sleep(0.01)
    prescale = int(round(25_000_000.0 / (4096 * cfg.PWM_FREQ))) - 1
    old = bus.read_byte_data(ADDR, MODE1)
    bus.write_byte_data(ADDR, MODE1, (old & 0x7F) | SLEEP)
    bus.write_byte_data(ADDR, PRESCALE, prescale)
    bus.write_byte_data(ADDR, MODE1, old)
    time.sleep(0.005)
    bus.write_byte_data(ADDR, MODE1, old | RESTART | AI)


def write_ticks(bus, ch, ticks):
    base = LED0_ON_L + 4 * ch
    bus.write_byte_data(ADDR, base + 0, 0)
    bus.write_byte_data(ADDR, base + 1, 0)
    bus.write_byte_data(ADDR, base + 2, ticks & 0xFF)
    bus.write_byte_data(ADDR, base + 3, (ticks >> 8) & 0xFF)


def release(bus, ch):
    base = LED0_ON_L + 4 * ch
    bus.write_byte_data(ADDR, base + 0, 0)
    bus.write_byte_data(ADDR, base + 1, 0)
    bus.write_byte_data(ADDR, base + 2, 0)
    bus.write_byte_data(ADDR, base + 3, 0x10)  # full-off


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    ch = int(sys.argv[1])
    if ch not in cfg.SERVO:
        print(f"Canal {ch} invalide (0=pan, 1=tilt).")
        sys.exit(1)

    axis = cfg.SERVO[ch]
    arg = sys.argv[2]
    us = axis["rest_us"] if arg == "rest" else int(arg)
    hold = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0

    clamped = cfg.clamp_us(ch, us)
    if clamped != us:
        print(f"{us} us hors plage sure {axis['name']} "
              f"[{axis['min_us']}, {axis['max_us']}] -> borne a {clamped} us.")
        us = clamped

    ticks = cfg.us_to_ticks(us)
    with SMBus(cfg.I2C_BUS) as bus:
        init(bus)
        try:
            print(f"{axis['name']} (canal {ch}) -> {us} us ({ticks} ticks), "
                  f"maintien {hold}s. Regarde/ecoute...")
            write_ticks(bus, ch, ticks)
            time.sleep(hold)
        except KeyboardInterrupt:
            print("\nInterrompu.")
        finally:
            release(bus, ch)
            print(f"Signal coupe sur canal {ch} - servo au repos (mou).")


if __name__ == "__main__":
    main()
