#
# This is loaded when a user activates the plugin. Include scripts here that should be
# loaded automatically at that point.
# 

# @load ./bar.bro 

event bro_init() { print "rot13 script is loaded"; }
