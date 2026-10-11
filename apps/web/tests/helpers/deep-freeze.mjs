/** Freezes a value and everything reachable from it, so any in-place mutation throws (modules are strict). */
export function deepFreeze(value, seen = new Set()) {
  if (typeof value !== "object" || value === null || seen.has(value)) return value;
  seen.add(value);
  Object.freeze(value);
  for (const key of Reflect.ownKeys(value)) deepFreeze(value[key], seen);
  return value;
}
