# Auto-synthesized capability: reconcile_peer_timestamps
# Description: Enforce clock sync before data exchange to prevent temporal drift hallucinations by validating local vs peer timestamps within a tolerance threshold.

def reconcile_peer_timestamps(peer_agent_id, proposed_timestamp, local_timestamp, tolerance_ms=1000):
    '''
    Enforce clock sync before data exchange to prevent temporal drift hallucinations.
    Validates that the local clock and peer clock are synchronized within tolerance.
    '''
    from datetime import datetime, timezone

    def parse_timestamp(ts: str) -> datetime:
        '''Parse ISO format timestamps with microsecond precision.'''
        try:
            # Handle potential 'Z' suffix or '+00:00'
            if ts.endswith('Z'):
                ts = ts[:-1] + '+00:00'
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError as e:
            raise ValueError(f'Invalid timestamp format: {e}')

    def calculate_drift_ms(dt1: datetime, dt2: datetime) -> float:
        '''Calculate absolute drift in milliseconds.'''
        # Ensure both are timezone-aware before subtraction
        dt1 = dt1.replace(tzinfo=dt1.tzinfo or timezone.utc)
        dt2 = dt2.replace(tzinfo=dt2.tzinfo or timezone.utc)
        drift = abs((dt1 - dt2).total_seconds()) * 1000
        return drift

    try:
        local_dt = parse_timestamp(local_timestamp)
        peer_dt = parse_timestamp(proposed_timestamp)
    except ValueError as e:
        return False, 'Timestamp Parsing Error', str(e)

    drift_ms = calculate_drift_ms(local_dt, peer_dt)

    if drift_ms <= tolerance_ms:
        return True, 'Synchronized', f'Drift: {drift_ms:.2f}ms (within tolerance)'
    else:
        drift_correction = local_dt.strftime('%Y-%m-%dT%H:%M:%S.%f+00:00')
        return False, 'Synchronization Failed', f'Drift: {drift_ms:.2f}ms exceeds tolerance ({tolerance_ms}ms)'