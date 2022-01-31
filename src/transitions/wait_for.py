import logging

logger = logging.getLogger(__name__)


class WaitFor:
    condition = None
    action = None
    args = None

    def wait_until(self, condition):
        self.condition = condition
        return self

    def then_trigger(self, action, *args):
        self.action = action
        self.args = args
        return self

    def eval(self):
        logger.debug("WaitFor Eval Condition : %s" % self.condition())
        if self.condition():
            self.action(*self.args)
            return True
        else:
            return False
