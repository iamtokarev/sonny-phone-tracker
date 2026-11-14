export function readMigratedPreference(
  storage: Pick<Storage, "getItem" | "setItem"> | null,
  key: string,
  oldKey: string,
): string | null {
  const current = storage?.getItem(key) ?? null;
  if (current !== null || storage === null) return current;

  const previous = storage.getItem(oldKey);
  if (previous !== null) {
    try {
      storage.setItem(key, previous);
    } catch {
      // The saved choice still applies when storage is read-only.
    }
  }
  return previous;
}
