import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import MarkdownIt from "markdown-it";

const markdown = new MarkdownIt();

// Verify exact filename casing even on case-insensitive developer filesystems.
function existsWithExactCase(root, target) {
  let current = root;
  for (const part of path.relative(root, target).split(path.sep).filter(Boolean)) {
    if (!fs.existsSync(current) || !fs.statSync(current).isDirectory()
      || !fs.readdirSync(current).includes(part)) return false;
    current = path.join(current, part);
  }
  return fs.existsSync(current);
}

export function checkMarkdownLinks(root, files, repositoryFiles) {
  root = path.resolve(root);
  const errors = [];
  let checked = 0;
  for (const file of files) {
    const source = path.resolve(root, file);
    const visit = (tokens, line = 1) => {
      for (const token of tokens) {
        const at = token.map ? token.map[0] + 1 : line;
        const href = token.type === "link_open" ? token.attrGet("href")
          : token.type === "image" ? token.attrGet("src") : null;
        if (href && !/^(?:[a-z][a-z0-9+.-]*:|\/\/|#)/i.test(href)) {
          const pathname = href.split(/[?#]/, 1)[0];
          if (pathname) {
            checked += 1;
            let target;
            try {
              const decoded = decodeURIComponent(pathname);
              target = decoded.startsWith("/") ? path.resolve(root, `.${decoded}`)
                : path.resolve(path.dirname(source), decoded);
            } catch {
              errors.push(`${file}:${at}: invalid URL encoding: ${href}`);
              continue;
            }
            const relative = path.relative(root, target);
            const published = !repositoryFiles || repositoryFiles.some((item) =>
              item === relative || item.startsWith(`${relative}${path.sep}`));
            if (!published || relative === ".." || relative.startsWith(`..${path.sep}`)
              || path.isAbsolute(relative) || !existsWithExactCase(root, target)) {
              errors.push(`${file}:${at}: missing repository file or incorrect case: ${href}`);
            }
          }
        }
        if (token.children) visit(token.children, at);
      }
    };
    visit(markdown.parse(fs.readFileSync(source, "utf8"), {}));
  }
  return { checked, errors };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = fileURLToPath(new URL("../", import.meta.url));
  const repositoryFiles = [...new Set(execFileSync("git", ["ls-files", "--cached", "--others",
    "--exclude-standard", "-z"], { cwd: root, encoding: "utf8" })
    .split("\0").filter((file) => file && fs.existsSync(path.join(root, file))))];
  const files = repositoryFiles.filter((file) => file.endsWith(".md"));
  const { checked, errors } = checkMarkdownLinks(root, files, repositoryFiles);
  if (errors.length) {
    console.error(errors.join("\n"));
    process.exitCode = 1;
  } else {
    console.log(`Checked ${checked} local link targets in ${files.length} Markdown files (including filename case).`);
    console.log("External URLs, fragment anchors and raw HTML are outside this check.");
  }
}
