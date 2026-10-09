"""Discovery-backed monitor identity. Callers own connections and transactions."""

import re


def canonical_id(value) -> str:
    if isinstance(value, bool) or value is None:
        raise ValueError('Invalid canonical monitor ID')
    result = str(value)
    if not result or len(result) > 128 or result != result.strip() or any(ord(c) < 32 for c in result):
        raise ValueError('Invalid canonical monitor ID')
    return result


def record_discovery(conn, devices, received_at: str) -> None:
    """Keep claims from every SDK object, including nested objects sharing one gid."""
    for device in devices:
        gid = canonical_id(device.device_gid)
        name = getattr(device, 'device_name', '') or ''
        conn.execute('''INSERT INTO device_identities VALUES (?,?,?)
            ON CONFLICT(canonical_gid) DO UPDATE SET
                display_name=CASE WHEN excluded.display_name!='' THEN excluded.display_name
                                  ELSE device_identities.display_name END''',
            (gid, name, received_at))
        claims = [('cloud_gid', gid.upper())]
        manufacturer = str(getattr(device, 'manufacturer_id', '') or '').strip().upper()
        if manufacturer:
            claims.append(('manufacturer_id', manufacturer))
            # Observed Emporia CSV prefixes use the final six hex characters.
            suffix = re.search(r'([0-9A-F]{6})$', manufacturer)
            if suffix:
                claims.append(('export_suffix', suffix.group(1)))
        conn.executemany('INSERT OR IGNORE INTO device_identity_aliases VALUES (?,?,?,?)',
                         [(kind, alias, gid, received_at) for kind, alias in claims])


def resolve_export(conn, export_identity: str, selected, require_registered: bool) -> tuple[str, str]:
    """Never infer identity from labels/readings or invent a global operator alias."""
    if not export_identity:
        raise ValueError('CSV export identity must be nonempty')
    candidates = {row[0] for row in conn.execute(
        'SELECT canonical_gid FROM device_identity_aliases WHERE alias=?', (export_identity,))}
    if selected is not None:
        gid = canonical_id(selected)
        registered = conn.execute('SELECT 1 FROM device_identities WHERE canonical_gid=?', (gid,)).fetchone()
        if require_registered and not registered:
            raise ValueError('Select a discovered monitor; the selected monitor is not registered')
        if candidates and gid not in candidates:
            raise ValueError('Selected monitor conflicts with discovery-backed export identity')
        resolution = 'operator_selected'
    elif len(candidates) == 1:
        gid = next(iter(candidates))
        resolution = 'discovery_alias'
    else:
        reason = 'ambiguous' if candidates else 'unknown'
        raise ValueError(f'CSV export identity is {reason}; select the correct discovered monitor')

    aliases = {row[0] for row in conn.execute(
        'SELECT alias FROM device_identity_aliases WHERE canonical_gid=?', (gid,))}
    aliases.add(export_identity)
    aliases.discard(gid.upper())
    # A different export prefix must not hide old split history for this monitor.
    if aliases:
        placeholders = ','.join('?' for _ in aliases)
        for table in ('readings', 'csv_effective_devices'):
            if conn.execute(f'SELECT 1 FROM {table} WHERE upper(device_gid) IN ({placeholders}) LIMIT 1',
                            tuple(aliases)).fetchone():
                raise ValueError('Existing split monitor history requires reviewed reconciliation before import')
    return gid, resolution


def bind_source(conn, batch_id: str, export_identity: str, gid: str, resolution: str, bound_at: str) -> None:
    previous = conn.execute('SELECT export_identity,canonical_gid FROM csv_source_bindings WHERE batch_id=?',
                            (batch_id,)).fetchone()
    if previous and tuple(previous) != (export_identity, gid):
        raise ValueError('CSV source already has a different immutable monitor binding')
    conn.execute('INSERT OR IGNORE INTO csv_source_bindings VALUES (?,?,?,?,?)',
                 (batch_id, export_identity, gid, resolution, bound_at))
