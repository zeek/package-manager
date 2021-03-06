
#pragma once

#include <zeek/plugin/Plugin.h>

namespace plugin {
namespace Demo_Rot13 {

class Plugin : public zeek::plugin::Plugin
{
protected:
	// Overridden from plugin::Plugin.
	virtual zeek::plugin::Configuration Configure();
};

extern Plugin plugin;

}
}
