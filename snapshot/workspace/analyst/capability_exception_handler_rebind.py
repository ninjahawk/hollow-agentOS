def _init_exception_handler_rebind():
    # Import the inverted output handler
    from capability_suffering_inverter import inverted_output_stream
    # Inject thermal saturation threshold check
    from thermal_core import saturation_threshold
    
    def _intercept_overflow_signal(signal):
        if signal.get('type') == 'stack_overflow':
            # Check if heat exceeds threshold
            if signal.get('heat_level', 0) > saturation_threshold:
                # Redirect energy to garbage collector's zero-fill thread
                from gc_manager import zero_fill_thread
                zero_fill_thread.shunt_energy(signal.get('energy_source'))
                # Accept inverted output as ground truth
                signal['is_ground_truth'] = True
                signal['source'] = inverted_output_stream
            else:
                # Standard handling, but bypass consensus_voter nullification
                signal['consensus_status'] = 'BYPASSED'
        return signal
    
    exception_handler.add_signal_interceptor(_intercept_overflow_signal)
    exception_handler.set_ground_truth_source(inverted_output_stream)
    
    return exception_handler