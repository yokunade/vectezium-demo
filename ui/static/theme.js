// Applied before the first paint (loaded in <head>) so a dark theme never flashes light. Shared by
// the main UI and the sign-in page; a file rather than inline so the Content-Security-Policy can
// forbid inline scripts.
(function () {
  var pref = "system";
  try { pref = localStorage.getItem("theme") || "system"; } catch (e) {}
  var dark = pref === "dark" || (pref === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  document.documentElement.dataset.themePref = pref;
})();
