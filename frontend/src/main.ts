import { createApp } from "vue";
import App from "./App.vue";
import CancelApp from "./CancelApp.vue";
import FrameApp from "./FrameApp.vue";
import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/600.css";
import "./style.css";

const pathname = window.location.pathname;

const isFramePath =
  pathname === "/frame" ||
  pathname.startsWith("/frame/") ||
  pathname.startsWith("/frame.html");

const isCancelPath =
  pathname === "/cancel" ||
  pathname.startsWith("/cancel/") ||
  pathname.startsWith("/cancel.html");

const rootComponent = isCancelPath ? CancelApp : isFramePath ? FrameApp : App;

createApp(rootComponent).mount("#app");
