"""
A module for zkg's notion of "user variables": named values required
by packages that the user can provide in a variety of ways, including
responses to zkg's input prompting.
"""
import os
import re
import readline

def slugify(string):
    """Returns file-system-safe, lower-case version of the input string.

    Any character sequence outside of ``[a-zA-Z0-9_]+`` gets replaced by a
    single underscore. If the variable has no value or the value is an
    empty string, returns the given default.
    """
    return re.sub(r'[^\w]+', '_', string, flags=re.ASCII).lower()


def _rlinput(prompt, prefill=''):
    """Variation of input() that supports pre-filling a value."""
    readline.set_startup_hook(lambda: readline.insert_text(prefill))
    try:
        return input(prompt)
    finally:
        readline.set_startup_hook()


class UserVar():
    """A class representing a single user variable.

    User variables have a name and an optional description. They
    resolve to a value using a cascade of mechanisms, including
    command-line arguments (via --user-var), environment variables,
    cached previous values, and user input. They may come with a
    default value.
    """
    def __init__(self, name, val=None, default=None, desc=None):
        self._name = name
        self._desc = desc or ''
        self._val = val
        self._default = default if default is not None else val

    def name(self):
        return self._name

    def desc(self):
        return self._desc

    def set(self, val):
        self._val = val

    def val(self, fallback=None):
        return self._val if self._val is not None else fallback

    def default(self):
        return self._default

    def resolve(self, name, config, user_var_args=None, force=False):
        """Populates user variables with updated values and returns them.

        This function resolves the variable in the following order:

        (1) Use any value provided on the command line via --user-var
        (2) If force is not used, prompt the user for input
        (3) use an environment variables of the same name,
        (4) retrieve from the provided config parser's "user_vars" section,
        (5) use the default value of the user variable.

        The resolved value is stored with the instance (to be
        retrieved via .val() in the future) and also returned.

        Args:
            name (str): the requesting entity, e.g. a package name

            config (configparser.ConfigParser): the zkg configuration

            user_var_args (list of UserVar): user-var instances
                provided via command line

            force (bool): whether to skip prompting for input

        Returns:
            str: the resulting variable value

        Raises:
            ValueError: when we couldn't produce a value for the variable
        """
        val = None
        source = None

        if user_var_args:
            for uvar in user_var_args:
                if uvar.name() == self._name:
                    val = uvar.val()
                    source = 'command line'
                    break

        if val is None:
            val = os.environ.get(self._name)
            if val:
                source = 'environment'

        if source:
            print('"{}" will use value of "{}" ({}) from {}: {}'.format(
                name, self._name, self._desc, source, val))
            self._val = val
            return val

        if val is None:
            # Try to re-use a cached value in the subsequent prompt
            val = config.get('user_vars', self._name, fallback=self._default)

        if force:
            if val is None:
                raise ValueError(self._name)
            self._val = val
            return val

        desc = ' (' + self._desc + ')' if self._desc else ''
        print('"{}" requires a "{}" value{}: '.format(
            name, self._name, desc))
        self._val = _rlinput(self._name + ': ', val)

        return self._val

    @staticmethod
    def parse_arg(arg):
        """Parser for NAME=VAL format string used in command-line args."""
        try:
            name, val = arg.split('=', 1)
            return UserVar(name, val=val)
        except ValueError as error:
            raise ValueError('invalid user var argument "{}", must be NAME=VAR'
                             .format(arg)) from error

    @staticmethod
    def parse_dict(metadata_dict):
        """Returns list of UserVars from the metadata's 'user_vars' field.

        Args:
            metadata_dict (dict of str->str): the input metadata, e.g. from
                a configparser entry value.

        Returns:
            list of UserVar. If the 'user_vars' field is not present,
            an empty list is returned.  If malformed, returns None.
        """
        text = metadata_dict.get('user_vars')

        if not text:
            return []

        rval = []

        text = text.strip()
        # Example: LIBRDKAFKA_ROOT [/usr] "Path to librdkafka installation"

        entries = re.split(r'(\w+\s+\[.*\]\s+".*")\s+', text)
        entries = list(filter(None, entries))

        for entry in entries:
            mob = re.match(r'(\w+)\s+\[(.*)\]\s+"(.*)"', entry)

            if not mob:
                return None

            groups = mob.groups()

            if len(groups) != 3:
                return None

            rval.append(UserVar(groups[0], val=groups[1], desc=groups[2]))

        return rval
