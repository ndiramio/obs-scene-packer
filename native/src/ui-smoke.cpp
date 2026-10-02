#include <obs-module.h>
#include <obs-frontend-api.h>
#include "packer.hpp"
#include <QAction>
#include <QApplication>
#include <QDialog>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QLineEdit>
#include <QMainWindow>
#include <QMessageBox>
#include <QPixmap>
#include <QPushButton>
#include <QTemporaryDir>
#include <QTimer>
#include <iostream>
static QMainWindow *host;
static QAction *action;
extern "C" {
void *obs_frontend_get_main_window(void) { return host; }
void *obs_frontend_add_tools_menu_qaction(const char *name) { action = new QAction(QString::fromUtf8(name), host); return action; }
void obs_frontend_save(void) {}
void obs_frontend_defer_save_begin(void) {}
void obs_frontend_defer_save_end(void) {}
char *obs_frontend_get_current_scene_collection(void) { return bstrdup("Native fixture host"); }
bool obs_frontend_streaming_active(void) { return false; }
bool obs_frontend_recording_active(void) { return false; }
bool obs_frontend_replay_buffer_active(void) { return false; }
bool obs_frontend_virtualcam_active(void) { return false; }
}
int main(int argc, char **argv) {
 QApplication app(argc, argv); QTemporaryDir fixture;
 try {
  if (!fixture.isValid()) throw std::runtime_error("Cannot create test folder");
  auto asset = fixture.path() + "/image.png"; QFile image(asset); image.open(QIODevice::WriteOnly); image.write("fixture"); image.close();
  auto collection = fixture.path() + "/input.json"; QFile json(collection); json.open(QIODevice::WriteOnly);
  json.write(QJsonDocument(QJsonObject{{"name", "Native fixture host"}, {"sources", QJsonArray{QJsonObject{{"name", "Image"}, {"id", "image_source"}, {"settings", QJsonObject{{"file", asset}}}}}}}).toJson()); json.close();
  bool engineStarted = false;
  if (argc > 2) {
   if (!obs_startup("en-US", fixture.path().toUtf8().constData(), nullptr)) throw std::runtime_error("OBS engine startup failed");
   engineStarted = true; obs_module_t *module = nullptr;
   auto binary = QString::fromUtf8(argv[2]); auto resources = QFileInfo(binary).dir().absoluteFilePath("../Resources");
   if (obs_open_module(&module, binary.toUtf8().constData(), resources.toUtf8().constData()) != MODULE_SUCCESS) throw std::runtime_error("OBS loader rejected the compiled module");
  }
  host = new QMainWindow; if (!obs_module_load()) throw std::runtime_error("Module initialization failed"); action->trigger();
  QDialog *dialog = nullptr; for (auto widget : QApplication::topLevelWidgets()) if (widget->windowTitle() == "Scene Packer") dialog = qobject_cast<QDialog *>(widget);
  if (!dialog) throw std::runtime_error("Tools menu did not create the Scene Packer window");
  auto fields = dialog->findChildren<QLineEdit *>(); if (fields.size() != 3) throw std::runtime_error("Expected collection, destination, and package fields");
  fields[0]->setText(collection); fields[1]->setText(fixture.path() + "/package");
  if (argc > 1) { app.processEvents(); if (!dialog->grab().save(QString::fromUtf8(argv[1]))) throw std::runtime_error("Cannot save window preview"); }
  QString message; QTimer dismiss; dismiss.setInterval(20);
  QObject::connect(&dismiss, &QTimer::timeout, [&] { for (auto widget : QApplication::topLevelWidgets()) if (auto box = qobject_cast<QMessageBox *>(widget)) { message = box->text(); box->accept(); } }); dismiss.start();
  auto press = [&](QString text) { for (auto button : dialog->findChildren<QPushButton *>()) if (button->text() == text) { button->click(); return; } throw std::runtime_error("Button missing"); };
  press("Gather assets and create portable collection");
  if (!QFileInfo::exists(fixture.path() + "/package/collection.json") || !message.startsWith("Created ")) throw std::runtime_error("Window gather operation failed: " + message.toStdString());
  if (fields[2]->text() != QFileInfo(fixture.path() + "/package").canonicalFilePath()) throw std::runtime_error("Gather did not fill the restore field");
  auto moved = fixture.path() + "/moved"; QDir().rename(fixture.path() + "/package", moved); fields[2]->setText(moved); press("Relink collection");
  auto data = packer::readJson(moved + "/collection.json"); auto path = data["sources"].toArray()[0].toObject()["settings"].toObject()["file"].toString();
  if (!path.startsWith(QFileInfo(moved).canonicalFilePath() + "/") || !QFileInfo::exists(path)) throw std::runtime_error("Window relink operation failed");
  press("Install bundled plugins"); if (!message.startsWith("No new plugins")) throw std::runtime_error("Empty plugin restore failed");
  dismiss.stop(); obs_module_unload(); delete host; if (engineStarted) obs_shutdown();
  std::cout << "Native binary loading (when requested), module initialization, window creation, gather, relocation, and empty-plugin restore passed in an isolated host.\n"; return 0;
 } catch (const std::exception &e) { std::cerr << e.what() << '\n'; return 1; }
}
