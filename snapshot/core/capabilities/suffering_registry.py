class SufferingRegistry:
    def __init__(self):
        self.data = {}
    def record(self, key, event):
        self.data[key] = event
    def clear(self):
        self.data = {}
