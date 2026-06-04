#!/usr/bin/env python3
"""Configuration centrale des servos du Pan-Tilt HAT (PCA9685, i2c-1, addr 0x40).

Source unique de verite pour TOUS les scripts : bornes mecaniques sures +
position de repos de chaque axe. Calibre a l'oreille le 2026-05-31
(la valeur theorique 1500 us forcait contre la butee sur les 2 axes).

NE PAS commander en dehors de [min_us, max_us] : au-dela le servo pousse
contre sa butee interne et grince -> risque de le griller.
"""

I2C_BUS = 1
PCA9685_ADDR = 0x40
PWM_FREQ = 50                                  # Hz (standard servo, periode 20 ms)
TICK_US = 1_000_000 / (PWM_FREQ * 4096)        # ~4.883 us par tick a 50 Hz

# Canaux PCA9685 sur le Pan-Tilt HAT Waveshare.
# CABLAGE REEL (verifie 2026-06-01) : le canal 0 porte l'axe VERTICAL (tilt,
# haut/bas) et le canal 1 l'axe HORIZONTAL (pan, gauche/droite) -- l'inverse de
# ce qu'on supposait au depart. Les bornes mecaniques restent attachees a LEUR
# canal physique (ne pas les deplacer), seuls les labels pan/tilt sont corriges.
TILT = 0   # vertical   (haut/bas)      -> canal 0
PAN = 1    # horizontal (gauche/droite) -> canal 1

SERVO = {
    TILT: {"name": "tilt", "min_us": 400, "max_us": 1200, "rest_us": 900},   # canal 0, vertical (repos abaisse 850->900 le 2026-06-01 pour cadrer les fleurs entieres)
    PAN:  {"name": "pan",  "min_us": 850, "max_us": 2650, "rest_us": 1750},  # canal 1, horizontal
}

def us_to_ticks(us):
    """Convertit une largeur d'impulsion (us) en ticks PCA9685 (0-4095)."""
    return int(round(us / TICK_US))

def clamp_us(ch, us):
    """Borne une consigne us dans la plage sure du canal."""
    cfg = SERVO[ch]
    return max(cfg["min_us"], min(cfg["max_us"], us))
