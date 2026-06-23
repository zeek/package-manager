cmake_minimum_required(VERSION 3.5 FATAL_ERROR)
project(zkg)

find_package(
  Python3
  COMPONENTS Interpreter
  REQUIRED)

# Set up a virtual env to install `shiv` which we use for bundling zkg into a
# Python interpreter-loadable pyz file.
set(VENV "${CMAKE_CURRENT_BINARY_DIR}/_venv")
set(SHIV "${VENV}/bin/shiv")
add_custom_command(
  OUTPUT "${SHIV}"
  COMMENT "Preparing shiv"
  COMMAND ${Python3_EXECUTABLE} -m venv "${VENV}"
  COMMAND "${VENV}/bin/pip" install shiv==1.0.8
  VERBATIM)
add_custom_target(
  shiv_install ALL
  COMMENT "shiv install"
  DEPENDS "${SHIV}")

set(ZKG_PYZ "${CMAKE_CURRENT_BINARY_DIR}/zkg.pyz")
add_custom_command(
  OUTPUT "${ZKG_PYZ}"
  COMMENT "Bundling zkg"
  COMMAND "${SHIV}" -c zkg -o "${ZKG_PYZ}" zkg
  # TODO: Ideally this would also depend on anything in `zeekpkg/` changing.
  DEPENDS shiv_install pyproject.toml zkg
  VERBATIM)
add_custom_target(
  zkg_pyz ALL
  COMMENT "bundled zkg"
  DEPENDS "${ZKG_PYZ}")

# Bake discovered Python interpreter into wrapper invoking created zkg.pyz.
configure_file(zkg.sh.in "${CMAKE_CURRENT_BINARY_DIR}/zkg" @ONLY)

# Install both the bundle as well as the generated wrapper.
install(FILES "${ZKG_PYZ}" DESTINATION "share/${PROJECT_NAME}/lib")
install(PROGRAMS "${CMAKE_CURRENT_BINARY_DIR}/zkg" DESTINATION bin)

if (NOT ZEEK_MAN_INSTALL_PATH)
    set(ZEEK_MAN_INSTALL_PATH ${CMAKE_INSTALL_PREFIX}/share/man)
endif ()

install(FILES doc/man/zkg.1 DESTINATION ${ZEEK_MAN_INSTALL_PATH}/man1)
