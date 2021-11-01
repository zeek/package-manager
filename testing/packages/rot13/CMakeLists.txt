
cmake_minimum_required(VERSION 3.5)

project(Plugin)

include(ZeekPlugin)

zeek_plugin_begin(Demo Rot13)
zeek_plugin_cc(src/Plugin.cc)
zeek_plugin_bif(src/rot13.bif)
zeek_plugin_dist_files(README CHANGES COPYING VERSION)
zeek_plugin_end()
