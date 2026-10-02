#pragma once
#include <QJsonObject>
#include <QString>
#include <QStringList>
namespace packer {
struct Options { bool fonts = false; bool plugins = false; QStringList fontDirs; QStringList pluginDirs; };
QJsonObject readJson(const QString &path);
QString pack(const QString &collection, const QString &destination, const Options &options = {});
QString rebase(const QString &package);
QString installPlugins(const QString &package, const QString &destination = {});
QJsonValue at(const QJsonValue &node, const QJsonArray &trail);
QJsonValue put(QJsonValue node, const QJsonArray &trail, const QJsonValue &value, int offset = 0);
}
