"""Convert Aqara Home chart accessibility captures into deduplicated CSV readings."""
import argparse
import csv
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def parse_chart(text: str, device: str, metric: str, year: int) -> list[dict]:
    units = {'temperature': 'degF', 'humidity': 'percent', 'pressure': 'kPa'}
    if 'Time Zone: GMT-04:00' not in text and 'Time Zone: GMT-05:00' not in text:
        raise ValueError('Capture must show its timezone; this workflow expects Eastern time')
    if metric == 'temperature' and '℉' not in text:
        raise ValueError('Temperature capture must be in Fahrenheit')
    rows = []
    pattern = r'Description:\s*(\d{1,2}:\d{2})\s*(AM|PM)\s*(\d{2}/\d{2}):\s*(-?\d+(?:\.\d+)?)'
    for clock, period, day, value in re.findall(pattern, text):
        stamp = datetime.strptime(f'{year}/{day} {clock} {period}', '%Y/%m/%d %I:%M %p')
        stamp = stamp.replace(tzinfo=ZoneInfo('America/New_York'))
        rows.append(dict(device=device, timestamp=stamp.isoformat(), metric=metric,
                         value=value, unit=units[metric], source='aqara_app_chart'))
    if not rows:
        raise ValueError('No individual chart samples found; do not export an empty capture')
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--year', type=int, required=True)
    args = parser.parse_args()
    output = args.directory / 'readings.csv'
    rows = {}
    if output.exists():
        with output.open(newline='') as handle:
            for row in csv.DictReader(handle):
                rows[(row['device'], row['timestamp'], row['metric'])] = row
    for capture in args.directory.glob('*.ax.txt'):
        device, metric = capture.name.removesuffix('.ax.txt').rsplit('-', 1)
        for row in parse_chart(capture.read_text(), device, metric, args.year):
            rows[(row['device'], row['timestamp'], row['metric'])] = row
    temporary = output.with_suffix('.csv.tmp')
    with temporary.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=['device', 'timestamp', 'metric', 'value', 'unit', 'source'])
        writer.writeheader()
        writer.writerows(sorted(rows.values(), key=lambda row: (row['device'], row['timestamp'], row['metric'])))
    temporary.replace(output)
    print(f'{len(rows)} unique readings saved to {output}')


if __name__ == '__main__':
    main()
