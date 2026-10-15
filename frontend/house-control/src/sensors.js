export const sensorKey = row => JSON.stringify([row.source,row.sensor_id]);
export function sensorReading(row) {
  const metrics=row.metrics || {}, values=[];
  for (const [key,unit] of [['temperature_f','°F'],['humidity_pct','% RH'],['radon_pci_l','pCi/L'],['battery_pct','% battery']]) {
    if (Number.isFinite(metrics[key])) values.push(`${metrics[key].toFixed(1)} ${unit}`);
  }
  return values.join(' · ') || 'No recorded values';
}
