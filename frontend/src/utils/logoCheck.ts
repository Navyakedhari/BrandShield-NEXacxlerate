// Client-side pre-check of a logo file. Mirrors server/app/services/logo.py so the user gets instant feedback;
// the server stays the authority (it decides by the file's bytes and re-encodes the image), and its error is always shown.
export const LOGO_MAX_BYTES = 2 * 1024 * 1024;
export const LOGO_MIN_SIDE = 256;
export const LOGO_MIN_ASPECT = 0.8;
export const LOGO_MAX_ASPECT = 1.25;

export interface LogoCheck {
  ok: boolean;
  /** Same wording as the server where the rule is the same. Empty when ok. */
  error: string;
  width?: number;
  height?: number;
}

export function sniffImageFormat(head: Uint8Array): 'png' | 'jpg' | 'webp' | null {
  const eq = (off: number, bytes: number[]) => bytes.every((b, i) => head[off + i] === b);
  if (head.length >= 8 && eq(0, [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])) return 'png';
  if (head.length >= 3 && eq(0, [0xff, 0xd8, 0xff])) return 'jpg';
  if (head.length >= 12 && eq(0, [0x52, 0x49, 0x46, 0x46]) && eq(8, [0x57, 0x45, 0x42, 0x50])) return 'webp';
  return null;
}

/** Judge size/aspect rules from numbers (pure, unit-tested). */
export function checkDimensions(width: number, height: number): string {
  if (width < LOGO_MIN_SIDE || height < LOGO_MIN_SIDE) return `Logo must be at least ${LOGO_MIN_SIDE} x ${LOGO_MIN_SIDE} pixels`;
  const aspect = width / height;
  if (aspect < LOGO_MIN_ASPECT || aspect > LOGO_MAX_ASPECT) return 'Logo must be roughly square';
  return '';
}

async function readDimensions(file: File): Promise<{ width: number; height: number } | null> {
  try {
    if (typeof createImageBitmap === 'function') {
      const bmp = await createImageBitmap(file);
      const out = { width: bmp.width, height: bmp.height };
      bmp.close?.();
      return out;
    }
  } catch {
    /* fall through to <img> */
  }
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      resolve({ width: img.naturalWidth, height: img.naturalHeight });
      URL.revokeObjectURL(url);
    };
    img.onerror = () => {
      resolve(null);
      URL.revokeObjectURL(url);
    };
    img.src = url;
  });
}

export async function checkLogoFile(file: File): Promise<LogoCheck> {
  if (file.size > LOGO_MAX_BYTES) return { ok: false, error: 'Logo is larger than 2 MB' };
  const head = new Uint8Array(await file.slice(0, 16).arrayBuffer());
  if (!file.size || !sniffImageFormat(head)) return { ok: false, error: 'Logo must be a PNG, JPEG or WebP image' };
  const dims = await readDimensions(file);
  if (!dims) return { ok: false, error: 'Logo must be a valid PNG, JPEG or WebP image' };
  const err = checkDimensions(dims.width, dims.height);
  return err ? { ok: false, error: err, ...dims } : { ok: true, error: '', ...dims };
}
