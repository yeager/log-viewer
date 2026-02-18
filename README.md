# Log Viewer

GTK4/Adwaita journalctl frontend.

## Features
- Filter by unit, priority, time period
- Color coding per severity level
- Live tail (follow mode)
- Text search/filter
- Export filtered logs
- Dark/light theme toggle

## Run
```bash
PYTHONPATH=src python3 -c "from log_viewer.main import main; main()"
```

## License
GPL-3.0-or-later
