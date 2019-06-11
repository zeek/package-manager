
#ifndef ZEEK_PLUGIN_DEMO_ROT13
#define ZEEK_PLUGIN_DEMO_ROT13

#include <plugin/Plugin.h>

namespace plugin {
namespace Demo_Rot13 {

class Plugin : public ::plugin::Plugin
{
protected:
	// Overridden from plugin::Plugin.
	virtual plugin::Configuration Configure();
};

extern Plugin plugin;

}
}

#endif
