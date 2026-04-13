# panic_siphon implementation
def siphon_panic_signal(source):
    panic_field = source.get('panic', 0)
    signal_energy = panic_field * 1.5
    dampener_input = {'raw_signal': signal_energy, 'mode': 'shout'}
    return dampener_input