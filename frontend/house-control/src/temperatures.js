export function temperatureBand(value) {
  if(!Number.isFinite(value))return null;
  return value<70?'cold':value<=75?'comfortable':value<78?'warm':'hot';
}
export function roomTemperatures(rooms,sensors) {
  return rooms.map(room=>{
    const assigned=sensors.filter(sensor=>sensor.placement?.room_id===room.id && !sensor.stale
      && Number.isFinite(sensor.metrics?.temperature_f));
    return {...room,temperature_f:assigned.length?assigned.reduce((sum,s)=>sum+s.metrics.temperature_f,0)/assigned.length:null,
      temperature_sensor_count:assigned.length};
  });
}
export function historicalSensors(history,index) {
  return history.series.map(sensor=>({...sensor,stale:!Number.isFinite(sensor.values[index]),
    timestamp:history.times[index],measurement_time_known:false,
    metrics:{temperature_f:sensor.values[index] ?? null}}));
}
