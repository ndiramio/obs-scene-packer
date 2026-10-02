#include <obs-module.h>
#include <obs-frontend-api.h>
#include "packer.hpp"
#include <QAction>
#include <QCheckBox>
#include <QDialog>
#include <QDialogButtonBox>
#include <QDir>
#include <QFileDialog>
#include <QFormLayout>
#include <QHBoxLayout>
#include <QJsonArray>
#include <QJsonDocument>
#include <QLabel>
#include <QLineEdit>
#include <QMessageBox>
#include <QPointer>
#include <QProgressDialog>
#include <QPushButton>
#include <QSaveFile>
#include <QThread>
#include <QUrl>
#include <QVBoxLayout>
#include <functional>
#include <stdexcept>
OBS_DECLARE_MODULE()
MODULE_EXPORT const char *obs_module_name(void) { return "OBS Scene Packer"; }
MODULE_EXPORT const char *obs_module_description(void) { return "Gather and transfer scene collection assets without Python."; }
MODULE_EXPORT const char *obs_module_author(void) { return "Nick DiRamio"; }
static QPointer<QAction> menuAction;
static QPointer<QDialog> window;
static QString currentCollectionPath() {
 obs_frontend_save(); char *name = obs_frontend_get_current_scene_collection(); QString current = QString::fromUtf8(name); bfree(name);
 QDir folder(QDir::homePath() + "/Library/Application Support/obs-studio/basic/scenes");
 for (auto file : folder.entryList({"*.json"}, QDir::Files)) {
  try { auto path = folder.absoluteFilePath(file); if (packer::readJson(path)["name"].toString() == current) return path; } catch (const std::exception &) {}
 }
 return {};
}
static bool idle(QWidget *parent) {
 if (obs_frontend_streaming_active() || obs_frontend_recording_active() || obs_frontend_replay_buffer_active() || obs_frontend_virtualcam_active()) {
  QMessageBox::warning(parent, "Scene Packer", "Stop streaming, recording, replay buffer, and virtual camera before transferring assets."); return false;
 } return true;
}
static QString job(QWidget *parent, const std::function<QString()> &operation) {
 QProgressDialog progress("Gathering and checking files…", QString(), 0, 0, parent); progress.setWindowTitle("Scene Packer"); progress.setWindowModality(Qt::ApplicationModal); progress.setMinimumDuration(0); progress.setAutoClose(false);
 QString result, error;
 auto thread = QThread::create([&] { try { result = operation(); } catch (const std::exception &e) { error = QString::fromUtf8(e.what()); } });
 QObject::connect(thread, &QThread::finished, &progress, &QDialog::accept);
 thread->start(); progress.exec(); thread->wait(); delete thread;
 if (!error.isEmpty()) throw std::runtime_error(error.toStdString()); return result;
}
static void applyPaths(const QString &originalFile, const QString &package) {
 auto original = packer::readJson(originalFile); char *current = obs_frontend_get_current_scene_collection(); auto name = QString::fromUtf8(current); bfree(current);
 if (name != original["name"].toString()) throw std::runtime_error("Load the original collection before replacing its paths.");
 auto manifest = packer::readJson(package + "/manifest.json");
 struct Update { obs_source_t *source; obs_data_t *settings; obs_data_t *original; };
 QList<Update> updates; QMap<QString, QJsonObject> changes; QMap<QString, QString> uuids;
 try {
  for (auto value : manifest["references"].toArray()) {
   auto ref = value.toObject(); auto trail = ref["trail"].toArray();
   if (trail.size() < 4 || trail[2].toString() != "settings") throw std::runtime_error("Filter assets require importing the portable collection; no live paths changed.");
   auto entry = original[trail[0].toString()].toArray()[trail[1].toInt()].toObject(); auto sourceName = entry["name"].toString();
   uuids[sourceName] = entry["uuid"].toString();
   if (!changes.contains(sourceName)) {
    auto source = obs_get_source_by_name(sourceName.toUtf8().constData()); if (!source) throw std::runtime_error("Source missing: " + sourceName.toStdString());
    auto settings = obs_source_get_settings(source); auto settingsObject = QJsonDocument::fromJson(obs_data_get_json(settings)).object();
    updates.append({source, nullptr, settings}); changes[sourceName] = settingsObject;
    if (!uuids[sourceName].isEmpty() && QString::fromUtf8(obs_source_get_uuid(source)) != uuids[sourceName]) throw std::runtime_error("Source changed since export; export the collection again.");
   }
   QJsonArray local; for (int i = 3; i < trail.size(); ++i) local.append(trail[i]);
   if (packer::at(changes[sourceName], local) != packer::at(entry["settings"], local)) throw std::runtime_error("Source path changed since export; export again before applying.");
   auto target = QFileInfo(package + "/" + ref["relative"].toString()).canonicalFilePath(); if (target.isEmpty()) throw std::runtime_error("Packaged asset missing.");
   changes[sourceName] = packer::put(changes[sourceName], local, ref["uri"].toBool() ? QUrl::fromLocalFile(target).toString(QUrl::FullyEncoded) : target).toObject();
  }
  for (auto &update : updates) {
   auto sourceName = QString::fromUtf8(obs_source_get_name(update.source)); auto bytes = QJsonDocument(changes[sourceName]).toJson(QJsonDocument::Compact);
   update.settings = obs_data_create_from_json(bytes.constData()); if (!update.settings) throw std::runtime_error("Cannot create replacement source settings.");
  }
  obs_frontend_defer_save_begin();
  for (auto update : updates) obs_source_update(update.source, update.settings);
  obs_frontend_defer_save_end(); obs_frontend_save();
 } catch (...) {
  for (auto update : updates) { if (update.settings) obs_data_release(update.settings); obs_data_release(update.original); obs_source_release(update.source); }
  throw;
 }
 for (auto update : updates) { obs_data_release(update.settings); obs_data_release(update.original); obs_source_release(update.source); }
}
static void showWindow() {
 if (window) { window->show(); window->raise(); window->activateWindow(); return; }
 auto parent = static_cast<QWidget *>(obs_frontend_get_main_window()); auto dialog = new QDialog(parent); window = dialog;
 dialog->setAttribute(Qt::WA_DeleteOnClose); dialog->setWindowTitle("Scene Packer"); dialog->resize(660, 420); auto layout = new QVBoxLayout(dialog);
 auto intro = new QLabel("Gather files into scene folders, then transfer the complete package to another Mac.", dialog); intro->setWordWrap(true); layout->addWidget(intro);
 auto form = new QFormLayout; layout->addLayout(form);
 auto collection = new QLineEdit(dialog); auto destination = new QLineEdit(dialog); auto package = new QLineEdit(dialog);
 auto collectionRow = new QHBoxLayout; collectionRow->addWidget(collection); auto browse = new QPushButton("Browse…", dialog); auto active = new QPushButton("Use current collection", dialog); collectionRow->addWidget(browse); collectionRow->addWidget(active); form->addRow("Collection JSON", collectionRow);
 QObject::connect(browse, &QPushButton::clicked, dialog, [=] { auto path = QFileDialog::getOpenFileName(dialog, "Collection JSON", {}, "OBS collections (*.json)"); if (!path.isEmpty()) collection->setText(path); });
 QObject::connect(active, &QPushButton::clicked, dialog, [=] { auto path = currentCollectionPath(); if (path.isEmpty()) QMessageBox::information(dialog, "Scene Packer", "Export the collection through Scene Collection → Export, then select its JSON file."); else collection->setText(path); });
 auto destinationRow = new QHBoxLayout; destinationRow->addWidget(destination); auto choose = new QPushButton("Choose new folder…", dialog); destinationRow->addWidget(choose); form->addRow("New package folder", destinationRow);
 QObject::connect(choose, &QPushButton::clicked, dialog, [=] { auto path = QFileDialog::getSaveFileName(dialog, "Name a new package folder", QDir::homePath() + "/Documents/OBS Package"); if (!path.isEmpty()) destination->setText(path); });
 auto fonts = new QCheckBox("Include matched non-system fonts", dialog); auto plugins = new QCheckBox("Include installed third-party plugin bundles", dialog); auto replace = new QCheckBox("Also replace paths in the loaded original collection", dialog);
 layout->addWidget(fonts); layout->addWidget(plugins); layout->addWidget(replace);
 auto gather = new QPushButton("Gather assets and create portable collection", dialog); layout->addWidget(gather);
 QObject::connect(gather, &QPushButton::clicked, dialog, [=] {
  if (!idle(dialog)) return;
  try {
   auto source = collection->text().trimmed(), target = destination->text().trimmed(); if (source.isEmpty() || target.isEmpty()) throw std::runtime_error("Choose a collection and new package folder.");
   auto current = currentCollectionPath(); if (source == current) obs_frontend_save();
   packer::Options options; options.fonts = fonts->isChecked(); options.plugins = plugins->isChecked();
   auto result = job(dialog, [=] { return packer::pack(source, target, options); }); package->setText(QFileInfo(result).path());
   if (replace->isChecked()) applyPaths(source, QFileInfo(result).path());
   QMessageBox::information(dialog, "Package complete", "Created " + result + "\n\nCopy the entire package folder. Review requirements.json and manifest.json for fonts, plugins, and browser dependencies.");
  } catch (const std::exception &e) { QMessageBox::warning(dialog, "Scene Packer", QString::fromUtf8(e.what())); }
 });
 auto restoreIntro = new QLabel("On the destination Mac, select the moved package below.", dialog); layout->addWidget(restoreIntro);
 auto packageRow = new QHBoxLayout; packageRow->addWidget(package); auto browsePackage = new QPushButton("Browse…", dialog); packageRow->addWidget(browsePackage); form = new QFormLayout; form->addRow("Moved package", packageRow); layout->addLayout(form);
 QObject::connect(browsePackage, &QPushButton::clicked, dialog, [=] { auto path = QFileDialog::getExistingDirectory(dialog, "Package folder"); if (!path.isEmpty()) package->setText(path); });
 auto restoreRow = new QHBoxLayout; auto relink = new QPushButton("Relink collection", dialog); auto install = new QPushButton("Install bundled plugins", dialog); restoreRow->addWidget(relink); restoreRow->addWidget(install); layout->addLayout(restoreRow);
 QObject::connect(relink, &QPushButton::clicked, dialog, [=] { if (!idle(dialog)) return; try { auto root = package->text().trimmed(); auto result = job(dialog, [=] { return packer::rebase(root); }); QMessageBox::information(dialog, "Relink complete", "Import " + result + " using Scene Collection → Import."); } catch (const std::exception &e) { QMessageBox::warning(dialog, "Scene Packer", e.what()); } });
 QObject::connect(install, &QPushButton::clicked, dialog, [=] { if (!idle(dialog)) return; try { auto root = package->text().trimmed(); auto result = job(dialog, [=] { return packer::installPlugins(root); }); QMessageBox::information(dialog, "Plugin installation", result); } catch (const std::exception &e) { QMessageBox::warning(dialog, "Scene Packer", e.what()); } });
 auto close = new QDialogButtonBox(QDialogButtonBox::Close, dialog); QObject::connect(close, &QDialogButtonBox::rejected, dialog, &QDialog::close); layout->addWidget(close); dialog->show();
}
bool obs_module_load(void) {
 auto action = static_cast<QAction *>(obs_frontend_add_tools_menu_qaction("Scene Packer")); menuAction = action;
 if (!action) { blog(LOG_ERROR, "[Scene Packer] OBS Tools menu is unavailable."); return false; }
 QObject::connect(action, &QAction::triggered, action, [] { showWindow(); });
 blog(LOG_INFO, "[Scene Packer] Native plugin 0.2.0 loaded; Python is not required."); return true;
}
void obs_module_unload(void) { if (window) delete window.data(); if (menuAction) delete menuAction.data(); }
