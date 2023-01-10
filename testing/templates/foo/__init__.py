import zeekpkg.template
import zeekpkg.uservar

TEMPLATE_API_VERSION = "1.0.0"


class Package(zeekpkg.template.Package):
    def contentdir(self):
        return "package"

    def needed_user_vars(self):
        return ["name"]

    def validate(self, tmpl):
        if not tmpl.lookup_param("name"):
            raise zeekpkg.template.InputError("package requires a name")


class Readme(zeekpkg.template.Feature):
    def contentdir(self):
        return "readme"

    def needed_user_vars(self):
        return ["readme"]


class Template(zeekpkg.template.Template):
    def define_user_vars(self):
        return [
            zeekpkg.uservar.UserVar(
                "name", desc='the name of the package, e.g. "FooBar"'
            ),
            zeekpkg.uservar.UserVar(
                "readme", desc="Content of the README file", val="This is a README."
            ),
        ]

    def apply_user_vars(self, uvars):
        for uvar in uvars:
            if uvar.name() == "name":
                self.define_param("name", uvar.val())
                self.define_param("module", uvar.val().upper())
            if uvar.name() == "readme":
                self.define_param("readme", uvar.val())

    def package(self):
        return Package()

    def features(self):
        return [Readme()]
