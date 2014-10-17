from plumbum.commands.base import BaseCommand


def make_concurrent(self, rhs):
    if not isinstance(rhs, BaseCommand):
        raise TypeError("rhs must be an instance of BaseCommand")
    if isinstance(self, ConcurrentCommand):
        if isinstance(rhs, ConcurrentCommand):
            self.commands.extend(rhs.commands)
        else:
            self.commands.append(rhs)
        return self
    elif isinstance(rhs, ConcurrentCommand):
        rhs.commands.insert(0, self)
        return rhs
    else:
        return ConcurrentCommand(self, rhs)

BaseCommand.__and__ = make_concurrent

class ConcurrentPopen(object):
    def __init__(self, procs):
        self.procs = procs
        self.stdin = None
        self.stdout = None
        self.stderr = None
        self.encoding = None
        self.returncode = None
    @property
    def argv(self):
        return [getattr(proc, "argv", []) for proc in self.procs]
    def poll(self):
        if self.returncode is not None:
            return self.returncode
        rcs = [proc.poll() for proc in self.procs]
        if any(rc is None for rc in rcs):
            return None
        self.returncode = 0
        for rc in rcs:
            if rc != 0:
                self.returncode = rc
                break
        return self.returncode

    def wait(self):
        for proc in self.procs:
            proc.wait()
        return self.poll()
    def communicate(self, input=None):
        if input:
            raise ValueError("Cannot pass input to ConcurrentPopen.communicate")
        out_err_tuples = [proc.communicate() for proc in self.procs]
        self.wait()
        return tuple(zip(*out_err_tuples))

class ConcurrentCommand(BaseCommand):
    def __init__(self, *commands):
        self.commands = list(commands)
    def formulate(self, level=0, args=()):
        form = ["("]
        for cmd in self.commands:
            form.extend(cmd.formulate(level, args))
            form.append("&")
        return form + [")"]
    def popen(self, args=(), **kwargs):
        assert not args, "Cannot pass args to ConcurrentCommand.popen"
        return ConcurrentPopen([cmd.popen(**kwargs) for cmd in self.commands])


if __name__ == "__main__":
    from plumbum.cmd import ls, date, sleep
    c = ls & date & sleep[1]
    print(c())

    c = ls & date & sleep[1] & sleep["-z"]
    print(c.run())








