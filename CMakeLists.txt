cmake_minimum_required(VERSION 3.5 FATAL_ERROR)
project(zkg)

set(ZEEK_BIN_DIR ${CMAKE_INSTALL_PREFIX}/bin)
set(orig_file ${CMAKE_CURRENT_SOURCE_DIR}/zkg)
set(configed_file ${CMAKE_CURRENT_BINARY_DIR}/zkg)
configure_file(${orig_file} ${configed_file} @ONLY)

# Install zkg
install(DIRECTORY DESTINATION bin)
install(PROGRAMS ${configed_file} DESTINATION bin)

if ( NOT PY_MOD_INSTALL_DIR )
    # This is not a Zeek-bundled install. Default to "home"-style install.
    set(PY_MOD_INSTALL_DIR lib/python)
endif ()

# Install the Python module tree
install(DIRECTORY DESTINATION ${PY_MOD_INSTALL_DIR})
install(DIRECTORY zeekpkg DESTINATION ${PY_MOD_INSTALL_DIR})

if ( NOT ZEEK_MAN_INSTALL_PATH )
    set(ZEEK_MAN_INSTALL_PATH ${CMAKE_INSTALL_PREFIX}/share/man)
endif ()

install(FILES doc/man/zkg.1 DESTINATION ${ZEEK_MAN_INSTALL_PATH}/man1)

message(
    "\n=====================|  ZKG Build Summary  |===================="
    "\n"
    "\nInstall prefix:      ${CMAKE_INSTALL_PREFIX}"
    "\nPython module path:  ${PY_MOD_INSTALL_DIR}"
    "\nConfig path:         ${ZEEK_ZKG_CONFIG_DIR}"
    "\nState path:          ${ZEEK_ZKG_STATE_DIR}"
    "\n"
    "\n================================================================\n"
)
