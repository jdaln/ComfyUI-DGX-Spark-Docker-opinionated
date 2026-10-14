// Downloads the PDF that Markdown to Pages and Picture Page write, when the node finishes.
// ComfyUI shows images, video, audio, 3D models and text files among a run's outputs, but not
// PDFs, so without this the PDF would only be reachable on the server's disk.
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const SETTING = "DGXSparkTemplates.DownloadPdf";

app.registerExtension({
  name: "DGXSparkTemplates.PdfDownload",
  settings: [
    {
      id: SETTING,
      category: ["DGX Spark Templates", "Pages", "Download PDF"],
      name: "Download the PDF when a page node finishes",
      type: "boolean",
      defaultValue: true,
    },
  ],
  setup() {
    api.addEventListener("executed", ({ detail }) => {
      const files = detail?.output?.pdf;
      if (!files?.length || !app.extensionManager.setting.get(SETTING)) return;
      for (const file of files) {
        const params = new URLSearchParams({
          filename: file.filename,
          subfolder: file.subfolder ?? "",
          type: file.type ?? "output",
        });
        const link = document.createElement("a");
        link.href = api.apiURL(`/view?${params}`);
        link.download = file.filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
      }
    });
  },
});
