from __future__ import with_statement
import os
import sys
import glob
import errno
import shutil
import subprocess
import logging
import stat
import time
import platform
import six
import re

if sys.version_info >= (3, 2):
    # python 3.2 has the new-and-improved subprocess module
    from subprocess import Popen, PIPE
    has_new_subprocess = True
else:
    # otherwise, see if we have subprocess32
    try:
        from subprocess32 import Popen, PIPE
        has_new_subprocess = True
    except ImportError:
        from subprocess import Popen, PIPE
        has_new_subprocess = False

from tempfile import mkdtemp
from contextlib import contextmanager
from plumbum.path import Path, FSUser
from plumbum.remote_path import RemotePath
from plumbum.commands import CommandNotFound, ConcreteCommand
from plumbum.session import ShellSession
from plumbum.lib import _setdoc, ProcInfo
from plumbum.daemons import _win32_daemonize, _posix_daemonize

try:
    from pwd import getpwuid, getpwnam
    from grp import getgrgid, getgrnam
except ImportError:
    def getpwuid(x):
        return (None,)
    def getgrgid(x):
        return (None,)
    def getpwnam(x):
        raise OSError("`getpwnam` not supported")
    def getgrnam(x):
        raise OSError("`getgrnam` not supported")

logger = logging.getLogger("plumbum.local")

IS_WIN32 = os.name == "nt"

#===================================================================================================
# Local Paths
#===================================================================================================
class LocalPath(Path):
    """The class implementing local-machine paths"""

    __slots__ = ["_path"]
    CASE_SENSITIVE = not IS_WIN32

    def __init__(self, path):
        if isinstance(path, RemotePath):
            raise TypeError("LocalPath cannot be constructed from %r" % (path,))
        self._path = os.path.normpath(str(path))
    def __new__(cls, path):
        if isinstance(path, cls):
            return path
        return object.__new__(cls)
    def __str__(self):
        return self._path
    def _get_info(self):
        return self._path

    @property
    @_setdoc(Path)
    def basename(self):
        return os.path.basename(str(self))

    @property
    @_setdoc(Path)
    def dirname(self):
        return LocalPath(os.path.dirname(str(self)))

    @property
    @_setdoc(Path)
    def uid(self):
        uid = self.stat().st_uid
        name = getpwuid(uid)[0]
        return FSUser(uid, name)

    @property
    @_setdoc(Path)
    def gid(self):
        gid = self.stat().st_gid
        name = getgrgid(gid)[0]
        return FSUser(gid, name)

    @_setdoc(Path)
    def join(self, other):
        if isinstance(other, RemotePath):
            raise TypeError("Cannot join local path %s with %r" % (self, other))
        return LocalPath(os.path.join(str(self), str(other)))

    @_setdoc(Path)
    def list(self):
        return [self / fn for fn in os.listdir(str(self))]

    @_setdoc(Path)
    def isdir(self):
        return os.path.isdir(str(self))

    @_setdoc(Path)
    def isfile(self):
        return os.path.isfile(str(self))

    @_setdoc(Path)
    def exists(self):
        return os.path.exists(str(self))

    @_setdoc(Path)
    def stat(self):
        return os.stat(str(self))

    @_setdoc(Path)
    def glob(self, pattern):
        return [LocalPath(fn) for fn in glob.glob(str(self / pattern))]

    @_setdoc(Path)
    def delete(self):
        if not self.exists():
            return
        if self.isdir():
            shutil.rmtree(str(self))
        else:
            os.remove(str(self))

    @_setdoc(Path)
    def move(self, dst):
        if isinstance(dst, RemotePath):
            raise TypeError("Cannot move local path %s to %r" % (self, dst))
        shutil.move(str(self), str(dst))
        return LocalPath(dst)

    @_setdoc(Path)
    def copy(self, dst, override = False):
        if isinstance(dst, RemotePath):
            raise TypeError("Cannot copy local path %s to %r" % (self, dst))
        dst = LocalPath(dst)
        if override:
            dst.remove()
        if self.isdir():
            shutil.copytree(str(self), str(dst))
        else:
            shutil.copy2(str(self), str(dst))
        return dst

    @_setdoc(Path)
    def mkdir(self):
        if not self.exists():
            try:
                os.makedirs(str(self))
            except OSError:
                # directory might already exist (a race with other threads/processes)
                _, ex, _ = sys.exc_info()
                if ex.errno != errno.EEXIST:
                    raise

    @_setdoc(Path)
    def open(self, mode = "r"):
        return open(str(self), mode)

    @_setdoc(Path)
    def read(self):
        with self.open() as f:
            return f.read()

    @_setdoc(Path)
    def write(self, data):
        with self.open("w") as f:
            f.write(data)

    @_setdoc(Path)
    def chown(self, owner = None, group = None, recursive = None):
        if not hasattr(os, "chown"):
            raise OSError("os.chown() not supported")
        uid = self.uid if owner is None else (owner if isinstance(owner, int) else getpwnam(owner)[2])
        gid = self.gid if group is None else (group if isinstance(group, int) else getgrnam(group)[2])
        os.chown(str(self), uid, gid)
        if recursive or (recursive is None and self.isdir()):
            for subpath in self.walk():
                os.chown(str(subpath), uid, gid)

    @_setdoc(Path)
    def chmod(self, mode):
        if not hasattr(os, "chmod"):
            raise OSError("os.chmod() not supported")
        os.chmod(str(self), mode)

    @_setdoc(Path)
    def link(self, dst):
        if isinstance(dst, RemotePath):
            raise TypeError("Cannot create a hardlink from local path %s to %r" % (self, dst))
        if hasattr(os, "link"):
            os.link(str(self), str(dst))
        else:
            # windows: use mklink
            if self.isdir():
                local["cmd"]("/C", "mklink", "/D", "/H", str(dst), str(self))
            else:
                local["cmd"]("/C", "mklink", "/H", str(dst), str(self))
    @_setdoc(Path)
    def symlink(self, dst):
        if isinstance(dst, RemotePath):
            raise TypeError("Cannot create a symlink from local path %s to %r" % (self, dst))
        if hasattr(os, "symlink"):
            os.symlink(str(self), str(dst))
        else:
            # windows: use mklink
            if self.isdir():
                local["cmd"]("/C", "mklink", "/D", str(dst), str(self))
            else:
                local["cmd"]("/C", "mklink", str(dst), str(self))

class Workdir(LocalPath):
    """Working directory manipulator"""

    __slots__ = []
    def __init__(self):
        LocalPath.__init__(self, os.getcwd())
    def __hash__(self):
        raise TypeError("unhashable type")
    def __new__(cls):
        return object.__new__(cls)

    def chdir(self, newdir):
        """Changes the current working directory to the given one

        :param newdir: The destination director (a string or a ``LocalPath``)
        """
        if isinstance(newdir, RemotePath):
            raise TypeError("newdir cannot be %r" % (newdir,))
        logger.debug("Chdir to %s", newdir)
        os.chdir(str(newdir))
        self._path = os.path.normpath(os.getcwd())
    def getpath(self):
        """Returns the current working directory as a ``LocalPath`` object"""
        return LocalPath(self._path)
    @contextmanager
    def __call__(self, newdir):
        """A context manager used to ``chdir`` into a directory and then ``chdir`` back to
        the previous location; much like ``pushd``/``popd``.

        :param newdir: The destination director (a string or a ``LocalPath``)
        """
        prev = self._path
        self.chdir(newdir)
        try:
            yield
        finally:
            self.chdir(prev)

#===================================================================================================
# Environment
#===================================================================================================
class EnvPathList(list):
    __slots__ = ["_path_factory"]
    PATHSEP = os.path.pathsep
    def __init__(self, path_factory):
        self._path_factory = path_factory
    def append(self, path):
        list.append(self, self._path_factory(path))
    def extend(self, paths):
        list.extend(self, (self._path_factory(p) for p in paths))
    def insert(self, index, path):
        list.insert(self, index, self._path_factory(path))
    def index(self, path):
        list.index(self, self._path_factory(path))
    def __contains__(self, path):
        return list.__contains__(self, self._path_factory(path))
    def remove(self, path):
        list.remove(self, self._path_factory(path))
    def update(self, text):
        self[:] = [self._path_factory(p) for p in text.split(os.path.pathsep)]
    def join(self):
        return self.PATHSEP.join(str(p) for p in self)


class BaseEnv(object):
    """The base class of LocalEnv and RemoteEnv"""
    __slots__ = ["_curr", "_path", "_path_factory"]
    CASE_SENSITIVE = True

    def __init__(self, path_factory):
        self._path_factory = path_factory
        self._path = EnvPathList(path_factory)
        self._update_path()

    def _update_path(self):
        self._path.update(self.get("PATH", ""))

    @contextmanager
    def __call__(self, *args, **kwargs):
        """A context manager that can be used for temporal modifications of the environment.
        Any time you enter the context, a copy of the old environment is stored, and then restored,
        when the context exits.

        :param args: Any positional arguments for ``update()``
        :param kwargs: Any keyword arguments for ``update()``
        """
        prev = self._curr.copy()
        self.update(**kwargs)
        try:
            yield
        finally:
            self._curr = prev
            self._update_path()

    def __iter__(self):
        """Returns an iterator over the items ``(key, value)`` of current environment
        (like dict.items)"""
        return iter(self._curr.items())
    def __hash__(self):
        raise TypeError("unhashable type")
    def __len__(self):
        """Returns the number of elements of the current environment"""
        return len(self._curr)
    def __contains__(self, name):
        """Tests whether an environment variable exists in the current environment"""
        return (name if self.CASE_SENSITIVE else name.upper()) in self._curr
    def __getitem__(self, name):
        """Returns the value of the given environment variable from current environment,
        raising a ``KeyError`` if it does not exist"""
        return self._curr[name if self.CASE_SENSITIVE else name.upper()]
    def keys(self):
        """Returns the keys of the current environment (like dict.keys)"""
        return self._curr.keys()
    def items(self):
        """Returns the items of the current environment (like dict.items)"""
        return self._curr.items()
    def values(self):
        """Returns the values of the current environment (like dict.values)"""
        return self._curr.values()
    def get(self, name, *default):
        """Returns the keys of the current environment (like dict.keys)"""
        return self._curr.get((name if self.CASE_SENSITIVE else name.upper()), *default)

    def __delitem__(self, name):
        """Deletes an environment variable from the current environment"""
        name = name if self.CASE_SENSITIVE else name.upper()
        del self._curr[name]
        if name == "PATH":
            self._update_path()
    def __setitem__(self, name, value):
        """Sets/replaces an environment variable's value in the current environment"""
        name = name if self.CASE_SENSITIVE else name.upper()
        self._curr[name] = value
        if name == "PATH":
            self._update_path()
    def pop(self, name, *default):
        """Pops an element from the current environment (like dict.pop)"""
        name = name if self.CASE_SENSITIVE else name.upper()
        res = self._curr.pop(name, *default)
        if name == "PATH":
            self._update_path()
        return res
    def clear(self):
        """Clears the current environment (like dict.clear)"""
        self._curr.clear()
        self._update_path()
    def update(self, *args, **kwargs):
        """Updates the current environment (like dict.update)"""
        self._curr.update(*args, **kwargs)
        if not self.CASE_SENSITIVE:
            for k, v in list(self._curr.items()):
                self._curr[k.upper()] = v
        self._update_path()

    def getdict(self):
        """Returns the environment as a real dictionary"""
        self._curr["PATH"] = self.path.join()
        return dict((k, str(v)) for k, v in self._curr.items())

    @property
    def path(self):
        """The system's ``PATH`` (as an easy-to-manipulate list)"""
        return self._path

    def _get_home(self):
        if "HOME" in self:
            return self._path_factory(self["HOME"])
        elif "USERPROFILE" in self:
            return self._path_factory(self["USERPROFILE"])
        elif "HOMEPATH" in self:
            return self._path_factory(self.get("HOMEDRIVE", ""), self["HOMEPATH"])
        return None
    def _set_home(self, p):
        if "HOME" in self:
            self["HOME"] = str(p)
        elif "USERPROFILE" in self:
            self["USERPROFILE"] = str(p)
        elif "HOMEPATH" in self:
            self["HOMEPATH"] = str(p)
        else:
            self["HOME"] = str(p)
    home = property(_get_home, _set_home)
    """Get or set the home path"""

    @property
    def user(self):
        """Return the user name, or ``None`` if it is not set"""
        # adapted from getpass.getuser()
        for name in ('LOGNAME', 'USER', 'LNAME', 'USERNAME'):
            if name in self:
                return self[name]
        try:
            # POSIX only
            import pwd
        except ImportError:
            return None
        else:
            return pwd.getpwuid(os.getuid())[0]  # @UndefinedVariable


class LocalEnv(BaseEnv):
    """The local machine's environment; exposes a dict-like interface"""
    __slots__ = []
    CASE_SENSITIVE = not IS_WIN32

    def __init__(self):
        # os.environ already takes care of upper'ing on windows
        self._curr = os.environ.copy()
        BaseEnv.__init__(self, LocalPath)
        if IS_WIN32 and "HOME" not in self and self.home is not None:
            self["HOME"] = self.home

    def expand(self, expr):
        """Expands any environment variables and home shortcuts found in ``expr``
        (like ``os.path.expanduser`` combined with ``os.path.expandvars``)

        :param expr: An expression containing environment variables (as ``$FOO``) or
                     home shortcuts (as ``~/.bashrc``)

        :returns: The expanded string"""
        prev = os.environ
        os.environ = self.getdict()
        try:
            output = os.path.expanduser(os.path.expandvars(expr))
        finally:
            os.environ = prev
        return output

    def expanduser(self, expr):
        """Expand home shortcuts (e.g., ``~/foo/bar`` or ``~john/foo/bar``)

        :param expr: An expression containing home shortcuts

        :returns: The expanded string"""
        prev = os.environ
        os.environ = self.getdict()
        try:
            output = os.path.expanduser(expr)
        finally:
            os.environ = prev
        return output

#===================================================================================================
# Local Commands
#===================================================================================================
class LocalCommand(ConcreteCommand):
    __slots__ = []
    QUOTE_LEVEL = 2

    def __init__(self, executable, encoding = "auto"):
        ConcreteCommand.__init__(self, executable,
            local.encoding if encoding == "auto" else encoding)

    def __repr__(self):
        return "LocalCommand(%r)" % (self.executable,)

    def popen(self, args = (), cwd = None, env = None, **kwargs):
        if isinstance(args, six.string_types):
            args = (args,)
        return local._popen(self.executable, self.formulate(0, args),
            cwd = self.cwd if cwd is None else cwd, env = self.env if env is None else env,
            **kwargs)

#===================================================================================================
# Local Machine
#===================================================================================================
class LocalMachine(object):
    """The *local machine* (a singleton object). It serves as an entry point to everything
    related to the local machine, such as working directory and environment manipulation,
    command creation, etc.

    Attributes:

    * ``cwd`` - the local working directory
    * ``env`` - the local environment
    * ``encoding`` - the local machine's default encoding (``sys.getfilesystemencoding()``)
    """
    cwd = Workdir()
    env = LocalEnv()
    encoding = sys.getfilesystemencoding()
    uname = platform.uname()[0]

    def __init__(self):
        self._as_user_stack = []

    if IS_WIN32:
        _EXTENSIONS = [""] + env.get("PATHEXT", ":.exe:.bat").lower().split(os.path.pathsep)

        @classmethod
        def _which(cls, progname):
            progname = progname.lower()
            for p in cls.env.path:
                try:
                    filelist = dict((n.basename, n) for n in p.list())
                except OSError:
                    continue
                for ext in cls._EXTENSIONS:
                    n = progname + ext
                    if n in filelist:
                        return filelist[n]
            return None
    else:
        @classmethod
        def _which(cls, progname):
            for p in cls.env.path:
                try:
                    filelist = dict((n.basename, n) for n in p.list())
                except OSError:
                    continue
                if progname in filelist:
                    f = filelist[progname]
                    if not f.stat().st_mode & stat.S_IXUSR:
                        continue
                    return f
            return None

    @classmethod
    def which(cls, progname):
        """Looks up a program in the ``PATH``. If the program is not found, raises
        :class:`CommandNotFound <plumbum.commands.CommandNotFound>`

        :param progname: The program's name. Note that if underscores (``_``) are present
                         in the name, and the exact name is not found, they will be replaced
                         by hyphens (``-``) and the name will be looked up again

        :returns: A :class:`LocalPath <plumbum.local_machine.LocalPath>`
        """
        alternatives = [progname]
        if "_" in progname:
            alternatives.append(progname.replace("_", "-"))
        for pn in alternatives:
            path = cls._which(pn)
            if path:
                return path
        raise CommandNotFound(progname, list(cls.env.path))

    def path(self, *parts):
        """A factory for :class:`LocalPaths <plumbum.local_machine.LocalPath>`.
        Usage: ``p = local.path("/usr", "lib", "python2.7")``
        """
        parts2 = [str(self.cwd)]
        for p in parts:
            if isinstance(p, RemotePath):
                raise TypeError("Cannot construct LocalPath from %r" % (p,))
            parts2.append(self.env.expanduser(str(p)))
        return LocalPath(os.path.join(*parts2))

    def __getitem__(self, cmd):
        """Returns a `Command` object representing the given program. ``cmd`` can be a string or
        a :class:`LocalPath <plumbum.local_machine.LocalPath>`; if it is a path, a command
        representing this path will be returned; otherwise, the program name will be looked up
        in the system's ``PATH`` (using ``which``). Usage::

            ls = local["ls"]
        """
        if isinstance(cmd, LocalPath):
            return LocalCommand(cmd)
        elif not isinstance(cmd, RemotePath):
            if "/" in cmd or "\\" in cmd:
                # assume path
                return LocalCommand(local.path(cmd))
            else:
                # search for command
                return LocalCommand(self.which(cmd))
        else:
            raise TypeError("cmd must not be a RemotePath: %r" % (cmd,))

    def _popen(self, executable, argv, stdin = PIPE, stdout = PIPE, stderr = PIPE,
            cwd = None, env = None, new_session = False, **kwargs):
        if new_session:
            if has_new_subprocess:
                kwargs["start_new_session"] = True
            elif subprocess.mswindows:
                kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP 
            else:
                def preexec_fn(prev_fn = kwargs.get("preexec_fn", lambda: None)):
                    os.setsid()
                    prev_fn()
                kwargs["preexec_fn"] = preexec_fn

        if subprocess.mswindows and "startupinfo" not in kwargs and stdin not in (sys.stdin, None):
            kwargs["startupinfo"] = sui = subprocess.STARTUPINFO()
            if hasattr(subprocess, "_subprocess"):
                sui.dwFlags |= subprocess._subprocess.STARTF_USESHOWWINDOW  # @UndefinedVariable
                sui.wShowWindow = subprocess._subprocess.SW_HIDE  # @UndefinedVariable
            else:
                sui.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # @UndefinedVariable
                sui.wShowWindow = subprocess.SW_HIDE  # @UndefinedVariable
        
        if not has_new_subprocess and "close_fds" not in kwargs:
            if subprocess.mswindows and (stdin is not None or stdout is not None or stderr is not None):
                # we can't close fds if we're on windows and we want to redirect any std handle
                kwargs["close_fds"] = False
            else:
                kwargs["close_fds"] = True

        if cwd is None:
            cwd = self.cwd
        if env is None:
            env = self.env
        if isinstance(env, BaseEnv):
            env = env.getdict()

        if self._as_user_stack:
            argv, executable = self._as_user_stack[-1](argv)

        logger.debug("Running %r", argv)
        proc = Popen(argv, executable = str(executable), stdin = stdin, stdout = stdout,
            stderr = stderr, cwd = str(cwd), env = env, **kwargs)  # bufsize = 4096
        proc._start_time = time.time()
        proc.encoding = self.encoding
        proc.argv = argv
        return proc

    def daemonize(self, command, cwd = "/"):
        """
        On POSIX systems
        ~~~~~~~~~~~~~~~~
        Run ``command`` as a UNIX daemon: fork a child process to setpid, redirect std handles to /dev/null, 
        umask, close all fds, chdir to ``cwd``, then fork and exec ``command``. Returns a ``Popen`` process that
        can be used to poll/wait for the executed command (but keep in mind that you cannot access std handles) 
        
        On Windows
        ~~~~~~~~~~
        Run ``command`` as a "Windows daemon": detach from controlling console and create a new process group.
        This means that the command will not receive console events and would survive its parent's termination. 
        Returns a ``Popen`` object.
        
        .. note:: this does not run ``command`` as a system service, only detaches it from its parent.
    
        .. versionadded:: 1.3
        """
        if subprocess.mswindows:
            return _win32_daemonize(command, cwd)
        else:
            return _posix_daemonize(command, cwd)
    
    if sys.platform == "win32":
        def list_processes(self):
            """
            Returns information about all running processes (on Windows: using ``tasklist``)
            """
            import csv
            tasklist = local["tasklist"]
            lines = tasklist("/V", "/FO", "CSV").encode("utf8").splitlines()
            rows = csv.reader(lines)
            header = rows.next()
            imgidx = header.index('Image Name')
            pididx = header.index('PID')
            statidx = header.index('Status')
            useridx = header.index('User Name')
            for row in rows:
                yield ProcInfo(int(row[pididx]), row[useridx].decode("utf8"), 
                    row[statidx].decode("utf8"), row[imgidx].decode("utf8"))
    else:
        def list_processes(self):
            """
            Returns information about all running processes (on POSIX systems: using ``ps``)
            """
            ps = self["ps"]
            lines = ps("-e", "-o", "pid,uid,stat,args").splitlines()
            lines.pop(0) # header
            for line in lines:
                parts = line.strip().split()
                yield ProcInfo(int(parts[0]), int(parts[1]), parts[2], " ".join(parts[3:]))

    def pgrep(self, pattern):
        """
        Process grep: return information about all processes whose command-line args match the given regex pattern
        """
        pat = re.compile(pattern)
        for procinfo in self.list_processes():
            if pat.search(procinfo.args):
                yield procinfo 

    def session(self):
        """Creates a new :class:`ShellSession <plumbum.session.ShellSession>` object; this
        invokes ``/bin/sh`` and executes commands on it over stdin/stdout/stderr"""
        return ShellSession(self["sh"].popen())

    @contextmanager
    def tempdir(self):
        """A context manager that creates a temporary directory, which is removed when the context
        exits"""
        dir = self.path(mkdtemp())  # @ReservedAssignment
        try:
            yield dir
        finally:
            dir.delete()

    @contextmanager
    def as_user(self, username = None):
        """Run nested commands as the given user. For example::

            head = local["head"]
            head("-n1", "/dev/sda1")    # this will fail...
            with local.as_user():
                head("-n1", "/dev/sda1")

        :param username: The user to run commands as. If not given, root (or Administrator) is assumed
        """
        if IS_WIN32:
            if username is None:
                username = "Administrator"
            self._as_user_stack.append(lambda argv: (["runas", "/savecred", "/user:%s" % (username,),
                '"' + " ".join(str(a) for a in argv) + '"'], self.which("runas")))
        else:
            if username is None:
                self._as_user_stack.append(lambda argv: (["sudo"] + list(argv), self.which("sudo")))
            else:
                self._as_user_stack.append(lambda argv: (["sudo", "-u", username] + list(argv), self.which("sudo")))
        try:
            yield
        finally:
            self._as_user_stack.pop(-1)

    def as_root(self):
        """A shorthand for :func:`as_user("root") <plumbum.local_machine.LocalMachine.as_user>`"""
        return self.as_user()

    python = LocalCommand(sys.executable, encoding)
    """A command that represents the current python interpreter (``sys.executable``)"""

local = LocalMachine()
"""The *local machine* (a singleton object). It serves as an entry point to everything
related to the local machine, such as working directory and environment manipulation,
command creation, etc.

Attributes:

* ``cwd`` - the local working directory
* ``env`` - the local environment
* ``encoding`` - the local machine's default encoding (``sys.getfilesystemencoding()``)
"""
