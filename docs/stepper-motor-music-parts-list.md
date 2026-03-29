# Stepper Motor Music — Complete Parts List

### Everything you need to build a stepper motor music player

From localhost audio | @local_host_audio_ on Instagram

---

## Core Components (Required)

| # | Component | Spec | Qty | Est. Price | Notes |
|---|-----------|------|-----|-----------|-------|
| 1 | **ESP32 Dev Board** | ESP32-WROOM-32, USB serial | 1 | $8–12 | Must have USB-to-serial chip (CP2102 or CH340). Most ESP32 dev boards on Amazon work. Avoid boards labeled "ESP32-S3" or "ESP32-C3" — the firmware is written for the original ESP32. |
| 2 | **Stepper Motor Drivers** | TB6600 | 6–8 | $10–15 each | TB6600 is the recommended driver. Handles up to 4A per phase, 9–42V input. Has screw terminals for clean wiring. Supports microstepping (set to 16 microsteps). Cheaper drivers like A4988/DRV8825 work but are harder to wire and less forgiving. |
| 3 | **Stepper Motors** | NEMA 17, bipolar, 4-wire, 1.8° step angle | 6–8 | $8–12 each | Any standard NEMA 17 works. Rated current between 1.0–2.0A is ideal. Higher current = louder sound but more heat. 42mm body length is the most common size. |
| 4 | **Power Supply** | 12V or 24V DC, sized for your motor count | 1 | $15–25 | **Sizing rule:** (rated amps per motor) x (number of motors) x 1.2 = minimum supply amps. Example: 6 motors at 1.5A = 9A, with margin = 11A minimum. A 12V/15A (180W) supply covers 8 motors comfortably. 24V works too — TB6600 handles both. |
| 5 | **USB Cable** | USB-A to Micro-USB (or USB-C, match your ESP32) | 1 | $5 | Data-capable cable, not charge-only. If your ESP32 doesn't show up as a serial port, the cable is usually the problem. |
| 6 | **Signal Wire** | 24–28 AWG hookup wire, solid or stranded | 1 spool | $8–10 | For STEP, DIR, and ENA connections between ESP32 and drivers. Keep runs short (under 12 inches). |
| 7 | **Power Wire** | 18–20 AWG, stranded | 1 spool | $8–10 | For motor coil wiring and power supply connections. Heavier gauge handles the current without voltage drop. |

### Core total estimate: **$120–180** for a 6-motor build, **$150–230** for 8 motors

---

## Optional but Useful

| Component | Why | Est. Price |
|-----------|-----|-----------|
| **Multimeter** | Check continuity, measure voltage, catch shorts before they kill components. Invaluable for debugging wiring issues. | $15–25 |
| **Terminal Blocks** (screw type) | Clean signal connections between ESP32 and drivers without soldering. | $5–10 |
| **Breadboard** | Useful for prototyping the signal wiring before committing to a permanent layout. | $5–8 |
| **Small Fan** | Driver cooling during extended sessions. TB6600s run warm under load. | $8–12 |
| **Mounting Board** | Plywood, acrylic, or 3D-printed bracket to mount motors in a row. Makes filming easier and looks better on camera. | $5–20 |

---

## Wiring Overview

Each motor channel needs 3 signal connections from the ESP32 to its TB6600 driver:

- **STEP** — pulse signal that makes the motor move one step. Frequency = pitch.
- **DIR** — controls rotation direction (motors 1–6 only; motors 7–8 are fixed direction).
- **ENA** — enable pin. Wired always-on in hardware (ENA+ to +5V, ENA- to GND).

Plus **power wiring** from the supply to each driver, and from each driver to its motor.

### GPIO Pin Map

| Motor | STEP Pin | DIR Pin |
|-------|----------|---------|
| Motor 1 | GPIO 16 | GPIO 4 |
| Motor 2 | GPIO 17 | GPIO 13 |
| Motor 3 | GPIO 18 | GPIO 14 |
| Motor 4 | GPIO 19 | GPIO 26 |
| Motor 5 | GPIO 21 | GPIO 27 |
| Motor 6 | GPIO 22 | GPIO 32 |
| Motor 7 | GPIO 23 | — (STEP only) |
| Motor 8 | GPIO 25 | — (STEP only) |

> Motors 1–6 have firmware-controlled direction. Motors 7–8 spin one direction only.

### Per-Channel Wiring (TB6600)

| Connection | From | To |
|-----------|------|-----|
| STEP signal | ESP32 STEP GPIO | TB6600 PUL- |
| STEP reference | +5V logic supply | TB6600 PUL+ |
| DIR signal (motors 1–6) | ESP32 DIR GPIO | TB6600 DIR- |
| DIR reference | +5V logic supply | TB6600 DIR+ |
| ENA (always enabled) | GND | TB6600 ENA- |
| ENA reference | +5V logic supply | TB6600 ENA+ |
| Signal ground | ESP32 GND | TB6600 logic GND |
| Motor coils | TB6600 A+/A-/B+/B- | Motor wires (2 pairs) |
| Motor power | Power supply V+/V- | TB6600 VCC/GND |

### TB6600 DIP Switch Settings

Set microstepping to **16 microsteps** (consult your TB6600's label — DIP switch positions vary by manufacturer). Set current limit to match your motor's rated current.

---

## Power Budget Calculator

```
Rated current per motor:    _____ A
Number of motors:           _____
Subtotal (amps):            _____ A
Safety margin (x 1.2):     _____ A  ← minimum supply rating
Supply voltage:             _____ V  (12V or 24V)
Minimum wattage:            _____ W  (amps x volts)
```

**Example:** 6 motors x 1.5A = 9A x 1.2 = 10.8A minimum. At 12V = 130W supply.

---

## Software (Free)

You don't need to buy any software. The full stack runs on free tools:

| Software | Purpose |
|----------|---------|
| **Python 3.10+** | Runs the host pipeline (MIDI parsing, streaming) |
| **PlatformIO** | Builds and flashes the ESP32 firmware |
| **VS Code** (optional) | IDE with PlatformIO extension |
| **Node.js** (optional) | Only needed if you want the live dashboard |

The complete source code, firmware, and build instructions are available in the full build guide.

---

## Quick Start Path

1. **Buy the core components** (ESP32, TB6600 drivers, NEMA 17 motors, power supply, cables, wire)
2. **Wire one motor first** — get a single channel working before wiring all 6–8
3. **Flash the firmware** to the ESP32 using PlatformIO
4. **Install the Python host** package
5. **Run a test MIDI** — you should hear your first stepper motor music within an hour of wiring

The full build guide walks through every step with detailed instructions: https://localhostaudio.gumroad.com/l/code-and-complete-build-guide

---

## Want to hear a specific song on stepper motors?

I take song requests — you pick the song, I play it on the 8-motor setup and post the video to Instagram. I tag you when it goes live.

Request a song: https://ko-fi.com/localhostaudio/commissions

Follow on Instagram: @local_host_audio_

---

*localhost audio*
