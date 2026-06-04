#!/usr/bin/env python3
"""Arret d'urgence des servos du Pan-Tilt HAT (PCA9685 sur i2c-1, addr 0x40).
Coupe le signal PWM sur tous les canaux -> les servos ne forcent plus (deviennent mous).
A lancer des qu'un servo grince / force : python3 stop_servos.py
"""
from smbus2 import SMBus

ADDR = 0x40
LED0_ON_L = 0x06
FULL_OFF_H = 0x10  # bit 4 du registre OFF_H = sortie eteinte en permanence

with SMBus(1) as bus:
    for ch in range(16):                 # on coupe les 16 canaux par securite
        base = LED0_ON_L + 4 * ch
        bus.write_byte_data(ADDR, base + 0, 0)           # ON_L
        bus.write_byte_data(ADDR, base + 1, 0)           # ON_H
        bus.write_byte_data(ADDR, base + 2, 0)           # OFF_L
        bus.write_byte_data(ADDR, base + 3, FULL_OFF_H)  # OFF_H : full-off

print("Tous les canaux PCA9685 coupes - servos au repos (mous).")
