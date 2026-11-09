<p align="center"><img src="docs/images/icon.png" width="96" alt=""></p>

<h1 align="center">Frame Canvas</h1>

<p align="center"><b>Turn Art Mode on your Samsung The Frame into a calm, beautiful, living dashboard.</b></p>

<p align="center">
  <a href="https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fsparkensparken%2Fframe-canvas"><img src="https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg" alt="Add repository to my Home Assistant"></a>
</p>
<p align="center">
  <a href="https://github.com/sparkensparken/frame-canvas/releases"><img src="https://img.shields.io/github/v/release/sparkensparken/frame-canvas?label=version" alt="Version"></a>
  <img src="https://img.shields.io/badge/Home%20Assistant-add--on-41BDF5?logo=homeassistant&logoColor=white" alt="Home Assistant add-on">
  <img src="https://img.shields.io/badge/arch-aarch64%20%7C%20amd64-555" alt="Architectures">
  <a href="LICENSE"><img src="https://img.shields.io/github/license/sparkensparken/frame-canvas" alt="MIT license"></a>
</p>

Frame Canvas is a Home Assistant add-on that renders a 4K dashboard – clock, date, weather,
your calendars, camera snapshots, a news ticker and gorgeous background photography – and
shows it *as art* on The Frame. It refreshes every minute, keeps Art Mode's low-power, matte
look, and lets your Home Assistant sensors turn the TV on when you walk in and off when you leave.

<p align="center">
  <img src="docs/images/room-1.jpg" alt="Frame Canvas on The Frame in a Scandinavian living room" width="100%">
</p>

<table>
  <tr>
    <td width="33%"><img src="docs/images/room-3.jpg" alt="Paper theme with serif type on the wall"></td>
    <td width="33%"><img src="docs/images/room-2.jpg" alt="Large camera layout in an evening living room"></td>
    <td width="33%"><img src="docs/images/room-4.jpg" alt="Classic layout with a photo background"></td>
  </tr>
</table>

<p align="center"><sub>Illustrative mockups (AI-generated rooms with real Frame Canvas screenshots, Norwegian UI). Real screenshots below.</sub></p>

---

## Why?

The Frame is a great TV that pretends to be a painting. But a painting can't tell you that it's
going to rain at three, that football practice starts at half past five, or who's at the front door.
Frame Canvas keeps the art – and quietly adds the information you actually glance at.

No apps to sideload, no browser kiosk, no developer mode. It uses The Frame's own Art Mode,
so the TV's light sensor, matte display and power-saving behave exactly as Samsung intended.

## Features

| | |
|---|---|
| 🕰️ **Clock, date & week number** | Large, legible typography in four layouts |
| 🌦️ **Weather** | Free forecasts from MET Norway – worldwide, no API key |
| 📅 **Calendars** | Add any iCal link (Google, iCloud, Outlook). Colour per person, show/hide, reorder |
| 📷 **Cameras** | Picks up your Home Assistant cameras automatically – up to four snapshots |
| 📰 **News ticker** | Headlines from RSS feeds, slowly rotating like a ticker |
| 🖼️ **Background art** | Modern photography (Unsplash), Bing's photo of the day (4K) or public-domain paintings from the Cleveland Museum of Art – only wide images that fill the screen |
| 🎨 **Appearance** | Four symmetric layouts, five themes, accent colours, two typefaces, thin/regular/bold weights, clock size |
| 👓 **Always readable** | Measures each background's brightness and dims bright photos automatically; optional frosted panels behind the text |
| 🚶 **Presence control** | Use presence/motion sensors or your phone: TV on when you arrive, off when the room has been empty |
| 📻 **Radio** | One-tap internet radio to your Home Assistant speakers (or the TV) |
| 🔆 **TV settings** | Art Mode brightness, colour temperature, light sensor and motion timer from one place |
| 🌍 **English & Norwegian** | Follows your Home Assistant language, or pick one |
| 🛡️ **Gentle on the panel** | Subtle pixel shift every minute and rotating backgrounds |
| 🪶 **Lightweight** | One long-lived headless browser renders each frame in well under a second – about 5× less CPU than launching a browser every minute |
| 🔒 **Private by default** | The API only answers Home Assistant itself; nothing on your Wi‑Fi can read your cameras or control the TV |

## Gallery

<p align="center"><img src="docs/images/hero.jpg" alt="Classic layout with a photo background" width="100%"></p>
<p align="center"><sub><i>Classic layout · photo background · cameras · news ticker</i></sub></p>

| | |
|---|---|
| ![Centred clock over a Monet painting](docs/images/art.jpg) | ![Large camera layout, forest theme](docs/images/camera.jpg) |
| *Centred clock · paintings* | *Large camera · forest theme* |
| ![Paper theme with serif type](docs/images/paper.jpg) | ![Minimal night layout over photography](docs/images/minimal.jpg) |
| *Paper theme · serif* | *Minimal · night theme · bold* |

### Control panel

Everything is configured from a panel in the Home Assistant sidebar – with a live preview.

<p align="center"><img src="docs/images/control.png" alt="Frame Canvas control panel in Home Assistant" width="85%"></p>

## Installation

**One click:** press the **Add repository** button at the top of this page, then skip to step 3.

**Manually:**

1. In Home Assistant, go to **Settings → Apps (Add-ons) → App store**.
2. Open the **⋮** menu → **Repositories**, paste
   ```
   https://github.com/sparkensparken/frame-canvas
   ```
   and click **Add**.
3. Find **Frame Canvas** in the store → **Install**. It builds locally (5–10 minutes on a Home Assistant Green).
4. **Start** it and enable **Show in sidebar**.
5. Open **Frame Canvas** in the sidebar → **TV & sensors** → **Find TVs on the network** → **Select**.
6. Accept the connection prompt on the TV the first time.

That's it – the dashboard appears in Art Mode within a minute.

## Recommended TV settings

- **Night Mode → Off** (Art Mode → *Art Mode Options* → *Sleep Options*). Otherwise The Frame
  switches itself off in a dim room about a minute after turning on.
- **Power On with Mobile → On** (*Settings → General & Privacy → Network → Expert Settings*),
  so the TV can be woken over the network.
- Turn off any Art Mode slideshow so the TV doesn't rotate away from the dashboard.

## Requirements

- Samsung **The Frame** (tested on the 2025 LS03F; 2021+ models with the Art Mode API should work)
- **Home Assistant OS** or Supervised (Green, Yellow, Raspberry Pi 4/5, x86-64)
- TV and Home Assistant on the same local network

## How it works

1. A small web server renders the dashboard page with your settings.
2. A single, long-lived headless Chromium reloads the page and captures a 3840 × 2160 JPEG
   over the DevTools protocol (falls back to a one-off browser if needed).
3. The image is uploaded to The Frame's Art Mode over the local network and selected;
   the previous one is deleted, so nothing piles up on the TV.
4. Nothing is pushed while the TV is in standby – so it never wakes the TV by accident.

Everything runs locally. The add-on only reaches out for weather, news, background images
and the calendars you add.

## Resource use

Measured on the rendering step: about **0.6 s and ~2 s of CPU time per frame** with the persistent
browser (vs ~3 s / ~10 s when launching Chromium every minute). On a Home Assistant Green that's a
brief blip once a minute. Want even less? Set *Update the TV image every* to 2, 5 or 15 minutes –
the clock then simply updates less often.

## Security

- The control panel is served through **Home Assistant ingress**, so it's protected by your HA login.
- The add-on's port `8765` only answers **Home Assistant itself** (ingress and automations on
  `127.0.0.1`). Other devices on your network get `403 Forbidden` – they can't read camera snapshots,
  your calendars or control the TV. No CORS headers, so other websites can't reach it from your browser.
- The only open endpoint is the radio stream the TV fetches.
- Need the API from another machine? Set the `api_token` option and send
  `Authorization: Bearer <token>`.
- Calendar links (iCal secret addresses) are never returned by the API.
- Dependencies are pinned; text from feeds and calendars is escaped before it's shown.

## Automations

Home Assistant can drive Frame Canvas through its local API on `http://127.0.0.1:8765` –
for example a dimmer, minimal "night" look at 22:00:

```yaml
rest_command:
  frame_canvas_settings:
    url: http://127.0.0.1:8765/api/settings
    method: POST
    content_type: application/json
    payload: "{{ settings | tojson }}"

automation:
  - alias: "Frame Canvas – night look"
    triggers: [{ trigger: time, at: "22:00:00" }]
    actions:
      - action: rest_command.frame_canvas_settings
        data: { settings: { theme: natt, layout: minimal, show: { news: false } } }
```

A complete example package (status sensors, scripts, radio) is included in
[`frame_canvas/frame_tv.yaml`](frame_canvas/frame_tv.yaml).

## FAQ

**Can I watch the cameras live?** Art Mode only shows still images, so cameras refresh once a minute.

**Will it burn in?** The Frame is a QLED (LCD) panel, which doesn't suffer permanent burn-in like OLED.
Frame Canvas still shifts the layout by a few pixels every minute and rotates backgrounds.

**Can radio play while Art Mode is on?** Not on the TV itself – Samsung stops media playback in Art Mode.
Pick a Home Assistant speaker (e.g. a Chromecast or Sonos) instead and the TV stays in Art Mode.

**The TV keeps turning itself off.** Check *Night Mode* and *Sleep After* in the Art Mode options.

## Credits

- Weather: [MET Norway](https://api.met.no/) · Place search: [Open-Meteo](https://open-meteo.com/)
- Photography: [Unsplash](https://unsplash.com/) via [Lorem Picsum](https://picsum.photos/) ·
  Photo of the day: Bing · Paintings: [Cleveland Museum of Art Open Access](https://openaccess-api.clevelandart.org/) (CC0)
- TV control: [samsung-tv-ws-api](https://github.com/xchwarze/samsung-tv-ws-api)

Screenshots use demo data.

## Disclaimer

Frame Canvas is an independent hobby project and is not affiliated with, endorsed by or
sponsored by Samsung. "The Frame" and "Art Mode" are trademarks of Samsung Electronics.

## License

[MIT](LICENSE)
