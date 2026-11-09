# Frame Canvas

Turn Art Mode on a Samsung The Frame into a beautiful, living dashboard: clock, date, weather,
calendars, camera snapshots, a news ticker and background photography – refreshed every minute.

## Getting started

1. Start the add-on and open **Frame Canvas** in the sidebar.
2. Go to **TV & sensors** → **Find TVs on the network** → **Select** (the TV must be on).
3. Accept the connection prompt on the TV the first time.
4. Pick a layout, theme, background, calendars and cameras in the other tabs.

## Tips

- Turn **Night Mode** off in the TV's Art Mode options (*Art Mode Options → Sleep Options*),
  otherwise the TV may switch off in a dim room.
- Turn **Power On with Mobile** on (*Settings → General & Privacy → Network → Expert Settings*)
  so the TV can be woken automatically.
- Calendars are added with an iCal link (Google: *Secret address in iCal format*).
- Cameras and speakers are picked up from Home Assistant automatically.
- The interface follows your Home Assistant language (English or Norwegian) – change it at the top of the panel.

## Options

| Option | Description |
|---|---|
| `tv_ip` / `tv_mac` | Optional. Normally you choose the TV in the panel instead. |
| `only_when_art_mode` | Only push the dashboard while the TV is in Art Mode (recommended). |
| `auto_ha_speakers` | List Home Assistant media players as radio speakers. |
| `cameras` | Extra cameras outside Home Assistant: `name` + `snapshot_url` (JPEG) or `rtsp_url`. |
| `api_token` | Optional. Lets other machines on your network use the API with `Authorization: Bearer <token>`. Without it, only Home Assistant can. |

## Security & privacy

The panel is protected by your Home Assistant login (ingress). The add-on's API only answers Home Assistant
itself – other devices on your network get `403`. Set `api_token` if you need access from elsewhere.

## Privacy

Everything runs locally. The add-on only fetches weather (MET Norway), news (RSS), background
images and the calendars you add.
