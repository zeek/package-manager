#
# This is loaded unconditionally at Zeek startup. Include scripts here that should
# always be loaded.
#
# Normally, that will be only code that initializes built-in elements. Load
# your standard scripts in
# scripts/<plugin-namespace>/<plugin-name>/__load__.zeek instead.
#

@load ./init.zeek



event zeek_init() { print "rot13 plugin is loaded"; }
