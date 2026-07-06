import abc
import sys
import threading
from collections.abc import Callable
from typing import Any, TextIO

# A type for the kind of callable we use for activities:
# they return an error string upon completion.
UiCallable = Callable[..., str]


class Activity(abc.ABC):
    """An activity conducted by zkg, such as a package install, build, or
    package source refresh.
    """

    def __init__(self) -> None:
        # An error message to leave in case the activity didn't complete
        # successfully.
        self.error = ""

    @abc.abstractmethod
    def __call__(self) -> None:
        pass


class CallableActivity(Activity):
    """An activity defined by a callable passed on to us."""

    def __init__(self, call: UiCallable):
        super().__init__()
        self.call = call

    def __call__(self) -> None:
        self.error = self.call()


class ProgressActivity(Activity):
    """An activity for which progress is quantifiable."""

    def __init__(self, total: float = 1.0) -> None:
        super().__init__()
        self.total = total

    @abc.abstractmethod
    def __call__(self) -> None:
        pass

    def progress(self, delta: float) -> None:
        """Callback invoked by the activity as it unfolds, whenever it can
        report progress toward completion.
        """
        pass


class Worker(threading.Thread):
    """A worker executes an activity. It runs in the background (in the sense of
    Python's threading model, so not truly concurrently), and can be waited
    upon.
    """

    def __init__(
        self,
        activity: Activity,
    ) -> None:
        super().__init__()
        self.activity = activity

    def run(self) -> None:
        """Runs the activity in the background."""
        self.activity()

    def wait(
        self,
        out: TextIO | Any = sys.stdout,
    ) -> None:
        """Blocks until this activity ends, optionally writing liveness indicators.

        This never returns until this thread dies (i.e., is_alive() is False).
        When an output file object is provided, the method also indicates
        progress by writing a dot character to it once per second. This happens
        only when the file is a TTY. When a message is given, it gets written
        out first, regardless of TTY status. Any output always terminates with a
        newline.

        Args:
            msg (str): a message to write first.

            out (file-like object): the destination to write to.

        """
        is_tty = hasattr(out, "isatty") and out.isatty()

        while True:
            self.join(1.0)
            if not self.is_alive():
                break

            if out is not None and is_tty:
                out.write(".")
                out.flush()

        if out is not None and is_tty:
            out.write("\n")
            out.flush()


class UserInterface(abc.ABC):
    def __init__(self, verbosity: int = 0) -> None:
        self.verbosity = verbosity

    @abc.abstractmethod
    def debug(
        self,
        *msgs: str,
        prefix: str = "Debug:",
        sep: str = " ",
        end: str = "\n",
        flush: bool = False,
    ) -> None:
        """Very verbose messaging, usually to stdout."""
        pass

    @abc.abstractmethod
    def verbose(
        self,
        *msgs: str,
        prefix: str = "Verbose:",
        sep: str = " ",
        end: str = "\n",
        flush: bool = False,
    ) -> None:
        """Verbose messaging, usually to stdout."""
        pass

    @abc.abstractmethod
    def info(
        self,
        *msgs: str,
        prefix: str = "Info:",
        sep: str = " ",
        end: str = "\n",
        flush: bool = False,
    ) -> None:
        """Regular messaging, usually to stdout."""
        pass

    @abc.abstractmethod
    def warning(
        self,
        *msgs: str,
        prefix: str = "Warning:",
        sep: str = " ",
        end: str = "\n",
        flush: bool = False,
    ) -> None:
        """Warnings, usually to stderr."""
        pass

    @abc.abstractmethod
    def error(
        self,
        *msgs: str,
        prefix: str = "Error:",
        sep: str = " ",
        end: str = "\n",
        flush: bool = False,
    ) -> None:
        """Errors, usually to stderr."""
        pass

    @abc.abstractmethod
    def activity(self, act: Activity) -> str:
        """Launch an open-ended activity to completion.

        Once the activity completes, the returned string contains an error
        message if anything went wrong. otherwise the string is empty.
        """
        pass

    @abc.abstractmethod
    def call_activity(self, call: Callable[[], str]) -> str:
        """Launch an open-ended activity to completion.

        The activity is provided in form of the callable.  Once the activity
        completes, the returned string contains an error message if anything
        went wrong. otherwise the string is empty.
        """
        pass

    @abc.abstractmethod
    def progress_activity(self, act: ProgressActivity) -> str:
        """Launch a trackable activity to completion.

        This applies to completions that can track their own progress, knowing
        when they complete.  Once the activity completes, the returned string
        contains an error message if anything went wrong. otherwise the string
        is empty.
        """
        pass

    def confirmation_prompt(self, prompt: str, default_to_yes: bool = True) -> bool:
        """An interactive prompt to obtain confirmation from the user."""
        yes = {"y", "ye", "yes"}

        if default_to_yes:
            prompt += " [Y/n] "
        else:
            prompt += " [N/y] "

        choice = input(prompt).lower()

        if not choice:
            if default_to_yes:
                return True

            print("Abort.")
            return False

        if choice in yes:
            return True

        print("Abort.")
        return False


class PlainUI(UserInterface):
    """A basic plaintext UI, suitable for the console or redirection to files."""

    def __init__(
        self,
        verbosity: int = 0,
        stdout: TextIO = sys.stdout,
        stderr: TextIO = sys.stderr,
    ):
        super().__init__(verbosity=verbosity)
        self.stdout = stdout
        self.stderr = stderr

    def debug(
        self,
        *msgs: str,
        prefix: str = "",
        sep: str = " ",
        end: str = "\n",
        flush: bool = False,
    ) -> None:
        _ = prefix
        if self.verbosity >= 2:
            print(*msgs, sep=sep, end=end, file=self.stdout, flush=flush)

    def verbose(
        self,
        *msgs: str,
        prefix: str = "",
        sep: str = " ",
        end: str = "\n",
        flush: bool = False,
    ) -> None:
        _ = prefix
        if self.verbosity >= 1:
            print(*msgs, sep=sep, end=end, file=self.stdout, flush=flush)

    def info(
        self,
        *msgs: str,
        prefix: str = "Info:",
        sep: str = " ",
        end: str = "\n",
        flush: bool = False,
    ) -> None:
        print(*msgs, sep=sep, end=end, file=self.stdout, flush=flush)

    def warning(
        self,
        *msgs: str,
        prefix: str = "Warning:",
        sep: str = " ",
        end: str = "\n",
        flush: bool = False,
    ) -> None:
        if prefix:
            print(prefix, *msgs, sep=sep, end=end, file=self.stderr, flush=flush)
        else:
            print(*msgs, sep=sep, end=end, file=self.stderr, flush=flush)

    def error(
        self,
        *msgs: str,
        prefix: str = "Error:",
        sep: str = " ",
        end: str = "\n",
        flush: bool = False,
    ) -> None:
        if prefix:
            print(prefix, *msgs, sep=sep, end=end, file=self.stderr, flush=flush)
        else:
            print(*msgs, sep=sep, end=end, file=self.stderr, flush=flush)

    def activity(self, act: Activity) -> str:
        worker = Worker(act)
        worker.start()
        worker.wait(self.stdout)
        return act.error

    def call_activity(self, call: UiCallable) -> str:
        act = CallableActivity(call)
        worker = Worker(act)
        worker.start()
        worker.wait()
        return act.error

    def progress_activity(self, act: ProgressActivity) -> str:
        worker = Worker(act)
        worker.start()
        worker.wait()
        return act.error


class UIProxy:
    """A proxy for a UI, relaying all calls.

    This allows us to use a toplevel UI object whose implementation we can swap out,
    keeping the global UI working in other modules that imported from this module.
    A toplevel assignment would break the relationship.
    """

    def __init__(self) -> None:
        self.impl: UserInterface = PlainUI()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.impl, name)


UI: UIProxy = UIProxy()
