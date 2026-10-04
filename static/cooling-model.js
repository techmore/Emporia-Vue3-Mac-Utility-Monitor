/* Single-zone RC sandbox. Power is electrical W, capacity kWh/K, loss W/K. */
function coolingScenario({initial, outdoor, target, loss, capacity, power, cop, gains}) {
  if (![initial,outdoor,target,loss,capacity,power,cop,gains].every(Number.isFinite) || loss <= 0 || capacity <= 0 || power < 0 || cop <= 0 || gains < 0) throw new Error('Invalid cooling scenario');
  // Exact passive step; fractional thermostat duty prevents overshooting target.
  const dt = 1/6, decay = Math.exp(-loss/1000*dt/capacity);
  const equilibrium = outdoor+gains/loss;
  let cooled = initial, passive = initial, kwh = 0;
  for (let i=0;i<144;i++) {
    passive = equilibrium+(passive-equilibrium)*decay;
    const noCooling = equilibrium+(cooled-equilibrium)*decay;
    const fullDrop = power*cop/loss*(1-decay);
    const duty = fullDrop > 0 ? Math.min(1,Math.max(0,(noCooling-target)/fullDrop)) : 0;
    cooled = noCooling-fullDrop*duty;
    kwh += power/1000*dt*duty;
  }
  return {cooled, passive, kwh};
}
if (typeof window !== 'undefined') window.coolingScenario = coolingScenario;
if (typeof module !== 'undefined' && module.exports) module.exports = coolingScenario;
