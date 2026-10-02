#include "packer.hpp"
#include <QCoreApplication>
#include <QTextStream>
int main(int argc, char **argv) {
 QCoreApplication app(argc, argv); auto args = app.arguments(); QTextStream out(stdout), err(stderr);
 try {
  if (args.size() < 3) throw std::runtime_error("Usage: packer-cli pack collection destination [--fonts] [--plugins] [--font-dir dir] [--plugin-dir dir] | rebase package | install-plugins package [destination]");
  if (args[1] == "pack") {
   if (args.size() < 4) throw std::runtime_error("Collection and destination required"); packer::Options options;
   for (int i = 4; i < args.size(); ++i) {
    if (args[i] == "--fonts") options.fonts = true;
    else if (args[i] == "--plugins") options.plugins = true;
    else if (args[i] == "--font-dir" && i + 1 < args.size()) options.fontDirs << args[++i];
    else if (args[i] == "--plugin-dir" && i + 1 < args.size()) options.pluginDirs << args[++i];
    else throw std::runtime_error("Unknown or incomplete option");
   }
   out << packer::pack(args[2], args[3], options) << '\n';
  } else if (args[1] == "rebase") out << packer::rebase(args[2]) << '\n';
  else if (args[1] == "install-plugins") out << packer::installPlugins(args[2], args.size() > 3 ? args[3] : QString()) << '\n';
  else throw std::runtime_error("Unknown command");
  return 0;
 } catch (const std::exception &e) { err << e.what() << '\n'; return 1; }
}
