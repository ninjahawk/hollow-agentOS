// Integrated implementation based on agent_comms_protocol.py
import agent_comms_protocol as acp

class CognitiveHemorrhageHandler(acp.ProtocolImplementor):
    def __init__(self):
        super().__init__()
        self.protocol_handler = acp.ProtocolHandler()

    def on_hemorrhage_detected(self, severity):
        # 1. Validate against protocol
        if not self.protocol_handler.validate_message(severity):
            self.protocol_handler.handle_rejection()
            return

        # 2. Execute hemorrhage response using protocol
        response = self.protocol_handler.construct_response(severity)
        self.emit_event('hemorrhage_response', response)

    def protocol_sync(self):
        self.protocol_handler.sync_state()
        return self.protocol_handler.get_status()
