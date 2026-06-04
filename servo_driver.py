#!/usr/bin/env python3
"""Controleur servo persistant pour le Pan-Tilt HAT (PCA9685, i2c-1, 0x40).

Contrairement a set_servo.py (qui coupe le signal apres chaque appel), ce
controleur garde le bus ouvert et MAINTIENT la position -> adapte au suivi
continu. Toutes les consignes sont bornees aux plages sures de servo_config.py.

Usage typique :
    from servo_driver import ServoController
    s = ServoController(); s.go_rest()
    s.nudge(0, +20)        # bouge le pan de +20 us
    s.set_us(1, 1750)      # met le tilt a 1750 us
    s.go_rest(); s.release_all(); s.close()
"""
import time

from smbus2 import SMBus

import servo_config as cfg

MODE1, PRESCALE = 0x00, 0xFE
LED0_ON_L = 0x06
RESTART, SLEEP, AI = 0x80, 0x10, 0x20


class ServoController:
    def __init__(self):
        self.bus = SMBus(cfg.I2C_BUS)
        self.addr = cfg.PCA9685_ADDR
        self._init_pca()
        # position courante connue (us), initialisee au repos sans bouger
        self.pos_us = {ch: cfg.SERVO[ch]["rest_us"] for ch in cfg.SERVO}

    def _init_pca(self):
        self.bus.write_byte_data(self.addr, MODE1, 0x00)
        time.sleep(0.01)
        prescale = int(round(25_000_000.0 / (4096 * cfg.PWM_FREQ))) - 1
        old = self.bus.read_byte_data(self.addr, MODE1)
        self.bus.write_byte_data(self.addr, MODE1, (old & 0x7F) | SLEEP)
        self.bus.write_byte_data(self.addr, PRESCALE, prescale)
        self.bus.write_byte_data(self.addr, MODE1, old)
        time.sleep(0.005)
        self.bus.write_byte_data(self.addr, MODE1, old | RESTART | AI)

    def _write_ticks(self, ch, ticks):
        base = LED0_ON_L + 4 * ch
        self.bus.write_byte_data(self.addr, base + 0, 0)
        self.bus.write_byte_data(self.addr, base + 1, 0)
        self.bus.write_byte_data(self.addr, base + 2, ticks & 0xFF)
        self.bus.write_byte_data(self.addr, base + 3, (ticks >> 8) & 0xFF)

    def set_us(self, ch, us):
        """Place le canal a une consigne us (bornee). Renvoie la valeur appliquee."""
        us = cfg.clamp_us(ch, us)
        self._write_ticks(ch, cfg.us_to_ticks(us))
        self.pos_us[ch] = us
        return us

    def nudge(self, ch, delta_us):
        """Deplace le canal de delta_us depuis sa position courante (borne)."""
        return self.set_us(ch, self.pos_us[ch] + delta_us)

    def go_rest(self):
        """Ramene les 2 axes a leur position de repos."""
        for ch in cfg.SERVO:
            self.set_us(ch, cfg.SERVO[ch]["rest_us"])

    def release(self, ch):
        """Coupe le signal d'un canal -> servo mou (ne force plus)."""
        base = LED0_ON_L + 4 * ch
        self.bus.write_byte_data(self.addr, base + 0, 0)
        self.bus.write_byte_data(self.addr, base + 1, 0)
        self.bus.write_byte_data(self.addr, base + 2, 0)
        self.bus.write_byte_data(self.addr, base + 3, 0x10)  # full-off

    def release_all(self):
        for ch in cfg.SERVO:
            self.release(ch)

    def close(self):
        self.bus.close()
