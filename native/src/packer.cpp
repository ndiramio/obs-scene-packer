#include "packer.hpp"
#include <QCryptographicHash>
#include <QDir>
#include <QDirIterator>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonParseError>
#include <QProcess>
#include <QRegularExpression>
#include <QSaveFile>
#include <QSet>
#include <QSysInfo>
#include <QTemporaryDir>
#include <QUrl>
#include <QtEndian>
#include <algorithm>
#include <filesystem>
#include <functional>
#include <stdexcept>
#include <sys/stat.h>
namespace fs = std::filesystem;
namespace packer {
static void fail(const QString &message) { throw std::runtime_error(message.toStdString()); }
static fs::path fp(const QString &p) { return fs::u8path(p.toStdString()); }
static QString canonical(const QString &p) {
 auto expanded = p.startsWith("~/") ? QDir::homePath() + p.mid(1) : p;
 auto info = QFileInfo(expanded); auto result = info.canonicalFilePath(); if (!result.isEmpty()) return result;
 auto absolute = QDir::cleanPath(info.absoluteFilePath()); if (absolute.isEmpty()) return {};
 QStringList missing; QFileInfo parent(absolute);
 while (!parent.exists() && parent.absoluteFilePath() != "/") {
  missing.prepend(parent.fileName()); parent = QFileInfo(parent.dir().absolutePath());
 }
 auto base = parent.canonicalFilePath(); if (base.isEmpty()) return absolute;
 for (const auto &part : missing) base = QDir(base).filePath(part);
 return QDir::cleanPath(base);
}
static bool within(const QString &child, const QString &parent) { return child != parent && child.startsWith(parent.endsWith('/') ? parent : parent + "/"); }
static QByteArray hash(const QByteArray &value) { return QCryptographicHash::hash(value, QCryptographicHash::Sha256).toHex(); }
static QString safeName(QString value) {
 auto suffix = QString::fromLatin1(hash(value.toUtf8()).left(8));
 value.replace(QRegularExpression("[^\\w .-]"), "_");
 value = value.trimmed().left(80); if (value.isEmpty() || value == "." || value == "..") value = "Unnamed";
 return value + "-" + suffix;
}
static QByteArray readFile(const QString &path) {
 QFile f(path); if (!f.open(QIODevice::ReadOnly)) fail("Cannot read: " + path); return f.readAll();
}
static void writeFile(const QString &path, const QByteArray &bytes) {
 QSaveFile f(path); if (!f.open(QIODevice::WriteOnly) || f.write(bytes) != bytes.size() || !f.commit()) fail("Cannot write: " + path);
}
QJsonObject readJson(const QString &path) {
 QJsonParseError error; auto doc = QJsonDocument::fromJson(readFile(path), &error);
 if (error.error != QJsonParseError::NoError || !doc.isObject()) fail("Invalid JSON: " + path + ": " + error.errorString());
 return doc.object();
}
static void writeJson(const QString &path, const QJsonObject &object) { writeFile(path, QJsonDocument(object).toJson()); }
QJsonValue at(const QJsonValue &node, const QJsonArray &trail) {
 QJsonValue value = node;
 for (const auto &key : trail) {
  if (key.isString() && value.isObject()) { auto o = value.toObject(); if (!o.contains(key.toString())) fail("Collection field missing"); value = o[key.toString()]; }
  else if (key.isDouble() && value.isArray() && key.toInt() >= 0 && key.toInt() < value.toArray().size()) value = value.toArray()[key.toInt()];
  else fail("Collection structure changed");
 }
 return value;
}
QJsonValue put(QJsonValue node, const QJsonArray &trail, const QJsonValue &value, int offset) {
 if (offset == trail.size()) return value;
 auto key = trail[offset];
 if (key.isString() && node.isObject()) {
  auto o = node.toObject(); if (!o.contains(key.toString())) fail("Collection field missing");
  o[key.toString()] = put(o[key.toString()], trail, value, offset + 1); return o;
 }
 if (key.isDouble() && node.isArray()) {
  auto a = node.toArray(); auto i = key.toInt(); if (i < 0 || i >= a.size()) fail("Invalid array index");
  a[i] = put(a[i], trail, value, offset + 1); return a;
 }
 fail("Invalid reference trail"); return {};
}
static QJsonArray append(QJsonArray a, QJsonValue key) { a.append(key); return a; }
static void strings(const QJsonValue &node, QJsonArray trail, const std::function<void(QJsonArray, QString)> &visit) {
 if (node.isString()) visit(trail, node.toString());
 else if (node.isObject()) { auto o = node.toObject(); for (auto it = o.begin(); it != o.end(); ++it) strings(it.value(), append(trail, it.key()), visit); }
 else if (node.isArray()) { auto a = node.toArray(); for (int i = 0; i < a.size(); ++i) strings(a[i], append(trail, i), visit); }
}
static void copyPath(const QString &source, const QString &destination, bool preserveLinks = false, QSet<QString> parents = {}) {
 if (QFileInfo(source).isDir()) {
  auto resolved = canonical(source); if (!preserveLinks && parents.contains(resolved)) fail("Asset folder has a symlink cycle: " + source);
  auto target = canonical(destination); if (target == resolved || within(target, resolved)) fail("Copy destination cannot be inside its source directory: " + source);
 }
 if (!preserveLinks && QFileInfo(source).isDir()) {
  auto resolved = canonical(source);
  parents.insert(resolved); fs::create_directories(fp(destination));
  for (const auto &entry : fs::directory_iterator(fp(source))) {
   auto child = QString::fromStdString(entry.path().u8string());
   copyPath(child, destination + "/" + QFileInfo(child).fileName(), false, parents);
  }
  return;
 }
 auto copiedSource = preserveLinks ? source : canonical(source);
 if (!QFileInfo::exists(copiedSource)) fail("Missing linked asset: " + source);
 fs::create_directories(fp(QFileInfo(destination).path()));
#ifdef Q_OS_MACOS
 QProcess copy;
 copy.start("/usr/bin/ditto", {"--rsrc", "--extattr", "--acl", "--qtn", copiedSource, destination});
 if (!copy.waitForStarted() || !copy.waitForFinished(-1) || copy.exitCode() != 0)
  fail("Cannot copy " + source + ": " + QString::fromUtf8(copy.readAllStandardError()));
#else
 fs::copy(fp(copiedSource), fp(destination), fs::copy_options::recursive | fs::copy_options::copy_symlinks);
#endif
}
static QString localPath(QString value, const QString &base, QString key) {
 if (value.startsWith("file://")) { QUrl url(value); if (!url.host().isEmpty() && url.host() != "localhost") return {}; return canonical(url.host() == "localhost" ? url.path(QUrl::FullyDecoded) : url.toLocalFile()); }
 if (value.contains("://") || value.contains('\n') || value.isEmpty()) return {};
 if (value.startsWith("~/")) value = QDir::homePath() + value.mid(1);
 if (value.startsWith('/')) return canonical(value);
 if (QStringList{"file", "local_file", "path", "value", "text_file", "input"}.contains(key) && QFileInfo::exists(base + "/" + value)) return canonical(base + "/" + value);
 return {};
}
static QString replaceMatches(const QString &text, const QRegularExpression &regex, const std::function<QString(QRegularExpressionMatch)> &callback) {
 QString result; qsizetype end = 0; auto matches = regex.globalMatch(text);
 while (matches.hasNext()) { auto match = matches.next(); result += text.mid(end, match.capturedStart() - end); result += callback(match); end = match.capturedEnd(); }
 return result + text.mid(end);
}
struct WebBundle {
 QString stage, dependencies; QMap<QString, QString> paths; QJsonArray files; QSet<QString> warnings;
 QString transfer(QString url, const QString &origin, const QString &output) {
  auto trimmed = url.trimmed(); QUrl parsed(trimmed);
  if (trimmed.isEmpty() || trimmed.startsWith('#') || (!parsed.scheme().isEmpty() && parsed.scheme() != "file") || !parsed.host().isEmpty()) {
   if (parsed.scheme() == "http" || parsed.scheme() == "https" || !parsed.host().isEmpty()) warnings.insert("Remote resource remains online: " + trimmed);
   return url;
  }
  QString path = parsed.scheme() == "file" ? parsed.toLocalFile() : parsed.path(QUrl::FullyDecoded);
  if (!QDir::isAbsolutePath(path)) path = QFileInfo(origin).path() + "/" + path;
  path = canonical(path); if (!QFileInfo(path).isFile()) fail("Missing local browser dependency: " + path);
  if (!paths.contains(path)) { paths[path] = dependencies + "/" + hash(path.toUtf8()).left(12) + "-" + QFileInfo(path).fileName(); process(path, paths[path]); }
  auto relative = QDir(QFileInfo(output).path()).relativeFilePath(paths[path]);
  return QString::fromUtf8(QUrl::toPercentEncoding(relative, "/")) + (parsed.hasQuery() ? "?" + parsed.query(QUrl::FullyEncoded) : "") + (parsed.hasFragment() ? "#" + parsed.fragment(QUrl::FullyEncoded) : "");
 }
 QString css(QString text, const QString &origin, const QString &output) {
  text = replaceMatches(text, QRegularExpression("url\\(\\s*([\"']?)(.*?)\\1\\s*\\)", QRegularExpression::CaseInsensitiveOption), [&](auto m) { return "url(\"" + transfer(m.captured(2), origin, output) + "\")"; });
  return replaceMatches(text, QRegularExpression("@import\\s+([\"'])(.*?)\\1", QRegularExpression::CaseInsensitiveOption), [&](auto m) { return "@import \"" + transfer(m.captured(2), origin, output) + "\""; });
 }
 void process(const QString &origin, const QString &output) {
  QDir().mkpath(QFileInfo(output).path()); auto ext = QFileInfo(origin).suffix().toLower();
  if (ext == "html" || ext == "htm" || ext == "css") {
   auto bytes = readFile(origin); if (bytes.startsWith("\xEF\xBB\xBF")) bytes.remove(0, 3); auto text = QString::fromUtf8(bytes);
   if (text.toUtf8() != bytes) fail("HTML/CSS must be UTF-8: " + origin);
   if (ext == "css") text = css(text, origin, output);
   else {
    if (QRegularExpression("<base\\b[^>]*\\bhref\\s*=", QRegularExpression::CaseInsensitiveOption).match(text).hasMatch()) fail("HTML base href is unsupported: " + origin);
    auto rewriteTag = [&](QString tag) {
     return replaceMatches(tag, QRegularExpression("\\b(srcset|src|href|poster|data|style)\\s*=\\s*(?:([\"'])(.*?)\\2|([^\\s>]+))", QRegularExpression::CaseInsensitiveOption | QRegularExpression::DotMatchesEverythingOption), [&](auto m) {
      auto key = m.captured(1); auto value = m.captured(2).isEmpty() ? m.captured(4) : m.captured(3);
      value.replace("&amp;", "&").replace("&quot;", "\"").replace("&#39;", "'");
      if (key.compare("style", Qt::CaseInsensitive) == 0) value = css(value, origin, output);
      else if (key.compare("srcset", Qt::CaseInsensitive) == 0) {
       if (value.contains("data:")) { warnings.insert("Data URL srcset needs manual review: " + origin); return m.captured(); }
       QStringList candidates; for (auto item : value.split(',')) { auto parts = item.simplified().split(' '); if (parts[0].isEmpty()) continue; parts[0] = transfer(parts[0], origin, output); candidates << parts.join(' '); } value = candidates.join(", ");
      } else value = transfer(value, origin, output);
      value.replace("&", "&amp;").replace("\"", "&quot;"); return key + "=\"" + value + "\"";
     });
    };
    text = replaceMatches(text, QRegularExpression("<!--.*?-->|(<script\\b[^>]*>)(.*?)(</script\\s*>)|(<style\\b[^>]*>)(.*?)(</style\\s*>)|<[^!][^>]*>", QRegularExpression::CaseInsensitiveOption | QRegularExpression::DotMatchesEverythingOption), [&](auto m) {
     if (m.captured().startsWith("<!--")) return m.captured();
     if (!m.captured(1).isEmpty()) { if (!m.captured(2).trimmed().isEmpty()) warnings.insert("Inline JavaScript runtime loads need manual review: " + origin); return rewriteTag(m.captured(1)) + m.captured(2) + m.captured(3); }
     if (!m.captured(4).isEmpty()) return rewriteTag(m.captured(4)) + css(m.captured(5), origin, output) + m.captured(6);
     return rewriteTag(m.captured());
    });
   }
   writeFile(output, text.toUtf8());
  } else { copyPath(origin, output); if (ext == "js" || ext == "mjs") warnings.insert("JavaScript runtime loads and imports need manual review: " + origin); }
  files.append(QJsonObject{{"original", origin}, {"relative", QDir(stage).relativeFilePath(output)}});
 }
 QJsonObject result() { QStringList list = warnings.values(); std::sort(list.begin(), list.end()); return {{"files", files}, {"warnings", QJsonArray::fromStringList(list)}}; }
};
static quint16 u16(const QByteArray &data, qsizetype offset) {
 if (offset < 0 || offset + 2 > data.size()) throw std::runtime_error("Truncated font");
 return qFromBigEndian<quint16>(reinterpret_cast<const uchar *>(data.constData() + offset));
}
static quint32 u32(const QByteArray &data, qsizetype offset, bool little = false) {
 if (offset < 0 || offset + 4 > data.size()) throw std::runtime_error("Truncated binary");
 auto p = reinterpret_cast<const uchar *>(data.constData() + offset);
 return little ? qFromLittleEndian<quint32>(p) : qFromBigEndian<quint32>(p);
}
static QSet<QString> fontNames(const QString &file) {
 QSet<QString> names;
 try {
  if (QFileInfo(file).size() > 32 * 1024 * 1024) return names;
  auto raw = readFile(file); QList<quint32> offsets{0};
  if (raw.left(4) == "ttcf") { offsets.clear(); auto count = u32(raw, 8); if (count > 256) return names; for (quint32 i = 0; i < count; ++i) offsets << u32(raw, 12 + i * 4); }
  for (auto offset : offsets) {
   auto count = u16(raw, offset + 4); if (count > 256) continue;
   for (quint32 i = 0; i < count; ++i) {
    auto table = offset + 12 + i * 16; if (raw.mid(table, 4) != "name") continue;
    auto start = u32(raw, table + 8); auto entries = u16(raw, start + 2); auto storage = u16(raw, start + 4); if (entries > 4096) continue;
    for (int j = 0; j < entries; ++j) {
     auto rec = start + 6 + j * 12; auto platform = u16(raw, rec); auto id = u16(raw, rec + 6); if (id != 1 && id != 4 && id != 6 && id != 16) continue;
     auto size = u16(raw, rec + 8); auto pos = qsizetype(start) + storage + u16(raw, rec + 10); if (pos + size > raw.size()) continue;
     QString value;
     if (platform == 0 || platform == 3) { for (qsizetype k = pos; k + 1 < pos + size; k += 2) value += QChar(u16(raw, k)); }
     else { value = QString::fromLatin1(raw.mid(pos, size)); }
     names.insert(value.toCaseFolded());
    }
   }
  }
 } catch (const std::exception &) {}
 return names;
}
static QJsonObject plist(const QString &path) {
 QProcess p; p.start("/usr/bin/plutil", {"-convert", "json", "-o", "-", path});
 if (!p.waitForFinished(5000) || p.exitCode() != 0) fail("Cannot read plugin metadata: " + path);
 return QJsonDocument::fromJson(p.readAllStandardOutput()).object();
}
static QStringList architectures(const QString &binary) {
 QFile f(binary); if (!f.open(QIODevice::ReadOnly)) return {}; auto data = f.read(16384); QStringList result;
 auto name = [](quint32 cpu) { return cpu == 0x01000007 ? QString("x86_64") : cpu == 0x0100000C ? QString("arm64") : QString("unsupported"); };
 try {
  auto magic = u32(data, 0);
  if (magic == 0xFEEDFACF || magic == 0xFEEDFACE || magic == 0xCFFAEDFE || magic == 0xCEFAEDFE) result << name(u32(data, 4, magic == 0xCFFAEDFE || magic == 0xCEFAEDFE));
  else if (magic == 0xCAFEBABE || magic == 0xBEBAFECA || magic == 0xCAFEBABF || magic == 0xBFBAFECA) {
   bool little = magic == 0xBEBAFECA || magic == 0xBFBAFECA; auto count = u32(data, 4, little); if (count > 128) return {};
   int stride = magic == 0xCAFEBABF || magic == 0xBFBAFECA ? 32 : 20;
   for (quint32 i = 0; i < count; ++i) result << name(u32(data, 8 + i * stride, little));
  }
 } catch (const std::exception &) { return {}; }
 result.removeDuplicates(); result.sort(); return result;
}
static QStringList treePaths(const QString &root) {
 QStringList result; QDirIterator iterator(root, QDir::AllEntries | QDir::Hidden | QDir::System | QDir::NoDotAndDotDot, QDirIterator::Subdirectories);
 while (iterator.hasNext()) result << QDir(root).relativeFilePath(iterator.next()); result.sort(); return result;
}
static QStringList validateBundle(const QString &bundle) {
 if (!QFileInfo(bundle).isDir() || !bundle.endsWith(".plugin")) fail("Invalid plugin bundle: " + bundle);
 for (auto rel : treePaths(bundle)) {
  QFileInfo entry(bundle + "/" + rel);
  if (entry.isSymLink() && (!entry.exists() || !within(canonical(entry.filePath()), canonical(bundle)))) fail("Plugin has broken or external symlink; use its installer: " + entry.filePath());
 }
 auto info = plist(bundle + "/Contents/Info.plist"); auto executable = info["CFBundleExecutable"].toString();
 if (executable.isEmpty() || executable.contains('/') || executable == "." || executable == "..") fail("Invalid plugin executable: " + bundle);
 auto binary = bundle + "/Contents/MacOS/" + executable; if (!QFileInfo(binary).isFile()) fail("Plugin executable missing: " + binary);
 return architectures(binary);
}
static QString bundleDigest(const QString &bundle) {
 QCryptographicHash digest(QCryptographicHash::Sha256);
 for (auto rel : treePaths(bundle)) {
  auto path = bundle + "/" + rel; QFileInfo info(path); digest.addData(rel.toUtf8()); digest.addData(QByteArray(1, '\0'));
  if (info.isSymLink()) { digest.addData(QByteArray("link\0", 5)); digest.addData(QByteArray::fromStdString(fs::read_symlink(fp(path)).u8string())); }
  else if (info.isFile()) {
   struct stat st{}; if (::stat(path.toUtf8().constData(), &st) != 0) fail("Cannot stat plugin file");
   digest.addData(QByteArray("file\0", 5)); digest.addData(QByteArray::number(st.st_mode & 0777)); digest.addData(QByteArray(1, '\0'));
   QFile file(path); if (!file.open(QIODevice::ReadOnly)) fail("Cannot read plugin file");
   while (!file.atEnd()) digest.addData(file.read(1024 * 1024));
  } else if (info.isDir()) digest.addData(QByteArray("dir\0", 4));
 }
 return QString::fromLatin1(digest.result().toHex());
}
static QJsonObject requirements(const QJsonObject &data, const QString &stage, const Options &options) {
 QMap<QString, QSet<QString>> fonts, types;
 std::function<void(QJsonValue)> visit = [&](QJsonValue value) {
  if (value.isObject()) {
   auto node = value.toObject(); auto font = node["font"].toObject(); auto face = font["face"].toString();
   if (!face.isEmpty()) fonts[face].insert(font["style"].toString("Regular"));
   if (node["id"].isString() && node["settings"].isObject()) types[node["id"].toString()].insert(node["name"].toString("Unnamed"));
   for (auto it = node.begin(); it != node.end(); ++it) visit(it.value());
  } else if (value.isArray()) for (auto item : value.toArray()) visit(item);
 }; visit(data);
 auto fontDirs = options.fontDirs; if (fontDirs.isEmpty()) fontDirs = {QDir::homePath() + "/Library/Fonts", "/Library/Fonts", "/System/Library/Fonts"};
 QMap<QString, QStringList> matches; QMap<QString, QString> copied;
 if (!fonts.isEmpty()) for (auto dir : fontDirs) {
  QDirIterator it(dir, QDir::Files, QDirIterator::Subdirectories);
  while (it.hasNext()) {
   auto file = it.next(); if (!QStringList{"ttf", "otf", "ttc", "otc"}.contains(QFileInfo(file).suffix().toLower())) continue;
   auto names = fontNames(file); for (auto face : fonts.keys()) if (names.contains(face.toCaseFolded())) matches[face] << file;
  }
 }
 QJsonArray fontReport, typeReport, plugins;
 for (auto face : fonts.keys()) {
  QJsonArray files;
  for (auto file : matches[face]) {
   bool system = file.startsWith("/System/Library/"); QJsonObject item{{"original", file}, {"system_font", system}};
   if (options.fonts && !system) {
    if (!copied.contains(file)) { auto relative = "fonts/" + QString::fromLatin1(hash(file.toUtf8()).left(12)) + "-" + QFileInfo(file).fileName(); copyPath(file, stage + "/" + relative); copied[file] = relative; }
    item["relative"] = copied[file];
   } files.append(item);
  }
  QStringList styles = fonts[face].values(); styles.sort(); fontReport.append(QJsonObject{{"family", face}, {"styles", QJsonArray::fromStringList(styles)}, {"matches", files}, {"status", files.isEmpty() ? "not_found" : "found"}});
 }
 for (auto id : types.keys()) { QStringList names = types[id].values(); names.sort(); typeReport.append(QJsonObject{{"id", id}, {"sources", QJsonArray::fromStringList(names)}}); }
 auto pluginDirs = options.pluginDirs; if (pluginDirs.isEmpty()) pluginDirs = {QDir::homePath() + "/Library/Application Support/obs-studio/plugins", "/Library/Application Support/obs-studio/plugins"};
 QSet<QString> names;
 for (auto folder : pluginDirs) for (auto name : QDir(folder).entryList({"*.plugin"}, QDir::Dirs, QDir::Name)) {
  auto bundle = canonical(folder + "/" + name); QJsonObject item{{"original", bundle}, {"bundle", name}};
  try { auto info = plist(bundle + "/Contents/Info.plist"); item["identifier"] = info["CFBundleIdentifier"]; item["version"] = info["CFBundleShortVersionString"].isUndefined() ? info["CFBundleVersion"] : info["CFBundleShortVersionString"]; }
  catch (const std::exception &) { if (options.plugins) throw; }
  if (options.plugins) {
   if (names.contains(name)) fail("Duplicate plugin name; choose a single plugin folder: " + name); names.insert(name);
   auto arch = validateBundle(bundle); if (arch.isEmpty()) fail("Cannot identify plugin architecture: " + name);
   auto relative = "plugins/" + name; copyPath(bundle, stage + "/" + relative, true); validateBundle(stage + "/" + relative);
   item["relative"] = relative; item["architectures"] = QJsonArray::fromStringList(arch); item["sha256"] = bundleDigest(stage + "/" + relative);
  }
  plugins.append(item);
 }
 return {{"fonts", fontReport}, {"required_source_and_filter_types", typeReport}, {"installed_user_plugins", plugins}, {"instructions", QJsonArray{"Install fonts with Font Book; family names stay unchanged.", "System fonts are reported but not copied.", "Restart OBS after restoring plugins; external runtimes and OBS-version compatibility require review.", "Plugin inventory includes all discovered bundles, not only those used by the collection."}}};
}
QString pack(const QString &collectionFile, const QString &destinationPath, const Options &options) {
 auto collection = canonical(collectionFile), destination = canonical(destinationPath); auto data = readJson(collection); auto originalData = data;
 if (QFileInfo::exists(destination)) fail("Choose a new package folder that does not exist yet.");
 struct Source { QString section; int index; QJsonObject object; };
 QList<Source> sources; QMap<QString, int> byName, byUuid; QMap<int, QSet<QString>> owners;
 for (auto section : {"sources", "groups", "transitions"}) { auto list = data[section].toArray(); for (int i = 0; i < list.size(); ++i) {
  int n = sources.size(); auto o = list[i].toObject(); sources.append({section, i, o});
  if (QString(section) != "transitions") { byName[o["name"].toString()] = n; if (!o["uuid"].toString().isEmpty()) byUuid[o["uuid"].toString()] = n; }
 }}
 std::function<void(int, QString, QSet<int>)> visit = [&](int n, QString owner, QSet<int> seen) {
  if (seen.contains(n)) return; seen.insert(n); owners[n].insert(owner);
  for (auto value : sources[n].object["settings"].toObject()["items"].toArray()) {
   auto item = value.toObject(); int child = byUuid.value(item["source_uuid"].toString(), -1);
   if (child < 0) child = byName.value(item["name"].toString(), -1); if (child >= 0) visit(child, owner, seen);
  }
 };
 for (int n = 0; n < sources.size(); ++n) if (sources[n].object["id"].toString() == "scene") visit(n, sources[n].object["name"].toString("Scene"), {});
 struct Reference { QJsonArray trail; QString original, path; QSet<QString> owners; };
 QList<Reference> refs; QMap<QString, QSet<QString>> assetOwners;
 for (int n = 0; n < sources.size(); ++n) {
  auto source = sources[n]; auto own = owners.value(n, QSet<QString>{"Collection"});
  strings(source.object, {}, [&](QJsonArray trail, QString value) {
   if (trail.isEmpty() || (trail[0].toString() != "settings" && trail[0].toString() != "filters")) return;
   auto path = localPath(value, QFileInfo(collection).path(), trail.last().toString()); if (path.isEmpty()) return;
   if (!QFileInfo::exists(path)) fail("Missing asset: " + path);
   if (QFileInfo(path).isDir() && (destination == path || within(destination, path))) fail("Package cannot be inside an asset directory.");
   QJsonArray full{source.section, source.index}; for (auto key : trail) full.append(key);
   refs.append({full, value, path, own}); assetOwners[path].unite(own);
  });
 }
 auto parent = QFileInfo(destination).path(); if (!QDir().mkpath(parent)) fail("Cannot create package parent folder.");
 QTemporaryDir stage(parent + "/.scene-packer-XXXXXX"); if (!stage.isValid()) fail("Cannot create package staging folder.");
 auto report = requirements(data, stage.path(), options); QJsonArray assets, references, browser, fontFiles; QMap<QString, QString> copied;
 for (auto ref : refs) {
  if (!copied.contains(ref.path)) {
   auto own = assetOwners[ref.path]; auto folder = own.size() > 1 ? QString("Shared") : safeName(*own.begin());
   auto relative = "assets/" + folder + "/" + hash(ref.path.toUtf8()).left(12) + "-" + QFileInfo(ref.path).fileName();
   auto target = stage.path() + "/" + relative; auto suffix = QFileInfo(ref.path).suffix().toLower();
   if (QFileInfo(ref.path).isFile() && (suffix == "html" || suffix == "htm")) {
    WebBundle web; web.stage = stage.path(); web.dependencies = QFileInfo(target).path() + "/" + QFileInfo(target).completeBaseName() + ".dependencies";
    web.paths[ref.path] = target; web.process(ref.path, target); browser.append(web.result());
   } else copyPath(ref.path, target);
   copied[ref.path] = relative; QStringList names = own.values(); names.sort();
   assets.append(QJsonObject{{"original", ref.path}, {"relative", relative}, {"scenes", QJsonArray::fromStringList(names)}});
  }
  auto newPath = destination + "/" + copied[ref.path]; bool uri = ref.original.startsWith("file://");
  data = put(data, ref.trail, uri ? QUrl::fromLocalFile(newPath).toString(QUrl::FullyEncoded) : newPath).toObject();
  references.append(QJsonObject{{"trail", ref.trail}, {"relative", copied[ref.path]}, {"uri", uri}});
 }
 data["name"] = data["name"].toString(QFileInfo(collection).completeBaseName()) + " Portable";
 QSet<QString> fontSet; for (auto font : report["fonts"].toArray()) for (auto match : font.toObject()["matches"].toArray()) if (match.toObject().contains("relative")) fontSet.insert(match.toObject()["relative"].toString());
 QStringList fontList = fontSet.values(); fontList.sort(); fontFiles = QJsonArray::fromStringList(fontList);
 QJsonObject manifest{{"version", 3}, {"collection", "collection.json"}, {"assets", assets}, {"references", references}, {"browser_dependencies", browser}, {"font_files", fontFiles}};
 writeJson(stage.path() + "/original-collection.json", originalData); writeJson(stage.path() + "/collection.json", data); writeJson(stage.path() + "/manifest.json", manifest); writeJson(stage.path() + "/requirements.json", report);
 if (QFileInfo::exists(destination) || !QDir().rename(stage.path(), destination)) fail("Cannot commit package: " + destination);
 stage.setAutoRemove(false); return destination + "/collection.json";
}
static QString packagedPath(const QString &root, const QString &relative, bool file = false) {
 if (relative.isEmpty() || QDir::isAbsolutePath(relative)) fail("Invalid package path.");
 auto path = canonical(root + "/" + relative);
 if (!within(path, root) || !QFileInfo::exists(path) || (file && !QFileInfo(path).isFile())) fail("Missing or unsafe package dependency: " + relative);
 return path;
}
QString rebase(const QString &package) {
 auto root = canonical(package); auto manifest = readJson(root + "/manifest.json"), data = readJson(root + "/collection.json");
 for (auto relative : manifest["font_files"].toArray()) packagedPath(root, relative.toString(), true);
 for (auto browser : manifest["browser_dependencies"].toArray()) for (auto entry : browser.toObject()["files"].toArray()) packagedPath(root, entry.toObject()["relative"].toString(), true);
 for (auto entry : manifest["references"].toArray()) {
  auto ref = entry.toObject(); auto path = packagedPath(root, ref["relative"].toString());
  data = put(data, ref["trail"].toArray(), ref["uri"].toBool() ? QUrl::fromLocalFile(path).toString(QUrl::FullyEncoded) : path).toObject();
 }
 writeJson(root + "/collection.json", data); return root + "/collection.json";
}
QString installPlugins(const QString &package, const QString &destinationPath) {
#ifndef Q_OS_MACOS
 fail("Plugin restoration is macOS only.");
#endif
 auto root = canonical(package); auto report = readJson(root + "/requirements.json");
 auto destination = canonical(destinationPath.isEmpty() ? QDir::homePath() + "/Library/Application Support/obs-studio/plugins" : destinationPath);
 auto arch = QSysInfo::buildCpuArchitecture(); QSet<QString> names, identifiers; QList<QPair<QString, QString>> pending; QStringList skipped;
 for (auto name : QDir(destination).entryList({"*.plugin"}, QDir::Dirs)) {
  try { auto id = plist(destination + "/" + name + "/Contents/Info.plist")["CFBundleIdentifier"].toString(); if (!id.isEmpty()) identifiers.insert(id); } catch (const std::exception &) {}
 }
 for (auto value : report["installed_user_plugins"].toArray()) {
  auto plugin = value.toObject(); if (!plugin.contains("relative")) continue;
  auto source = packagedPath(root, plugin["relative"].toString()); auto available = validateBundle(source);
  if (!available.contains(arch)) fail("Plugin architecture does not support this OBS process: " + QFileInfo(source).fileName() + " (" + arch + ")");
  auto digest = bundleDigest(source); if (digest != plugin["sha256"].toString()) fail("Plugin changed or is incomplete: " + source);
  auto name = QFileInfo(source).fileName(); if (names.contains(name)) fail("Duplicate packaged plugin: " + name); names.insert(name);
  auto target = destination + "/" + name;
  if (QFileInfo::exists(target)) {
   if (QFileInfo(target).isDir() && bundleDigest(target) == digest) { skipped << name; continue; }
   fail("Installed plugin differs; nothing replaced: " + target);
  }
  auto id = plugin["identifier"].toString(); if (!id.isEmpty() && identifiers.contains(id)) fail("Plugin identifier already installed: " + id); if (!id.isEmpty()) identifiers.insert(id);
  if (source == destination || within(destination, source) || within(source, destination)) fail("Installation folder must be outside plugin bundles.");
  pending.append({source, target});
 }
 if (pending.isEmpty()) return "No new plugins to install; already present: " + skipped.join(", ");
 if (!QDir().mkpath(destination)) fail("Cannot create plugin installation folder.");
 QTemporaryDir stage(destination + "/.scene-packer-install-XXXXXX"); if (!stage.isValid()) fail("Cannot stage plugin installation."); QStringList installed;
 try {
  for (auto pair : pending) copyPath(pair.first, stage.path() + "/" + QFileInfo(pair.first).fileName(), true);
  for (auto pair : pending) {
   if (QFileInfo::exists(pair.second) || !QDir().rename(stage.path() + "/" + QFileInfo(pair.first).fileName(), pair.second)) fail("Cannot install plugin: " + pair.second);
   installed << pair.second;
  }
 } catch (...) { for (auto target : installed) QDir(target).removeRecursively(); throw; }
 return "Installed " + QString::number(installed.size()) + " plugin bundles. Restart OBS before importing the collection.";
}
} // namespace packer
