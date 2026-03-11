# Emporia Energy Monitor

Python-based energy monitoring for Emporia Vue 3 devices with SQLite storage and Flask dashboard.

## Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Set your Emporia credentials
export EMPORIA_EMAIL="your@email.com"
export EMPORIA_PASSWORD="yourpassword"
export RATE_CENTS=11.04
```

## Usage

### 1. Start Polling (runs continuously)
```bash
python energy.py
```

Or run in background:
```bash
python energy.py &  # macOS/Linux
```

### 2. Start Web Dashboard
```bash
python web.py
```
Then open http://localhost:5000

### CLI Commands
```bash
python energy.py poll          # Single poll
python energy.py summary       # 24h summary
python energy.py hourly 7      # Hourly data for 7 days
python energy.py daily 30      # Daily data for 30 days
python energy.py latest        # Latest readings
```

## Features
- Per-circuit usage tracking
- Cost calculation at your rate ($0.1104/kWh default)
- Historical data in SQLite
- Charts: daily/hourly usage
- Monthly cost projection vs $150 budget
- API endpoints for Ollama integration

## Ollama Integration

Query your data via API:
```bash
curl http://localhost:5000/api/summary
```

Then feed to Ollama for insights like:
- "Am I on track for my monthly budget?"
- "Which circuits use the most power?"
- "Predict my next bill"
