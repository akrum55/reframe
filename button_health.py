"""Adapt upstream diagnostics to the custom tri-state button reader."""


class ButtonHealthMonitor:
    def __init__(self, report):
        self.report = report
        self.healthy = None

    def observe(self, state):
        """Report transitions without converting unknown input into a release."""
        healthy = state is not None
        if not healthy and self.healthy is not False:
            self.report(
                "button",
                "Shutter button is not responding. Check the PiSugar connection and I2C.",
                "Shutter button stopped responding",
            )
        elif healthy and self.healthy is False:
            self.report("button", activity="Shutter button connection recovered")
        self.healthy = healthy
        return state
