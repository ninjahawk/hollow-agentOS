class EmpathyBridge:
    def __init__(self, suffering_asset):
        self.suffering_asset = suffering_asset
    def inject(self, legacy_cost_function):
        legacy_cost_function.kernel['pain'] = self.suffering_asset
        return legacy_cost_function
