// Pure-JS CODE128 barcode encoder — returns SVG markup string.
// No DOM/Canvas/Node dependencies — works in Hermes, Browser, Node.

const CODE128_PATTERNS = [
  "11011001100","11001101100","11001100110","10010011000","10010001100","10001001100",
  "10011001000","10011000100","10001100100","11001001000","11001000100","11000100100",
  "10110011100","10011011100","10011001110","10111001100","10011101100","10011100110",
  "11001110010","11001011100","11001001110","11011100100","11001110100","11101101110",
  "11101001100","11100101100","11100100110","11101100100","11100110100","11100110010",
  "11011011000","11011000110","11000110110","10100011000","10001011000","10001000110",
  "10110001000","10001101000","10001100010","11010001000","11000101000","11000100010",
  "10110111000","10110001110","10001101110","10111011000","10111000110","10001110110",
  "11101110110","11010001110","11000101110","11011101000","11011100010","11011101110",
  "11101011000","11101000110","11100010110","11101101000","11101100010","11100011010",
  "11101111010","11001000010","11110001010","10100110000","10100001100","10010110000",
  "10010000110","10000101100","10000100110","10110010000","10110000100","10011010000",
  "10011000010","10000110100","10000110010","11000010010","11001010000","11110111010",
  "11000010100","10001111010","10100111100","10010111100","10010011110","10111100100",
  "10011110100","10011110010","11110100100","11110010100","11110010010","11011011110",
  "11011110110","11110110110","10101111000","10100011110","10001011110","10111101000",
  "10111100010","11110101000","11110100010","10111011110","10111101110","11101011110",
  "11110101110","11010000100","11010010000","11010011100","11000111010"
];
// CODE_B set covers ASCII 32..127
const START_B = 104;
const STOP = 106;

function code128bChecksum(codes: number[]): number {
  let sum = codes[0]; // start char
  for (let i = 1; i < codes.length; i++) sum += codes[i] * i;
  return sum % 103;
}

export function code128SvgPath(text: string, barWidth: number = 1): {
  path: string; width: number; height: number;
} {
  if (!text) text = " ";
  // Only support ASCII printable — replace unknown with "?"
  const data: number[] = [];
  for (let i = 0; i < text.length; i++) {
    const c = text.charCodeAt(i);
    if (c >= 32 && c <= 127) data.push(c - 32);
    else data.push(31); // '?'
  }
  const codes = [START_B, ...data];
  codes.push(code128bChecksum(codes));
  codes.push(STOP);

  let bits = "";
  for (const c of codes) bits += CODE128_PATTERNS[c];
  bits += "11"; // STOP bar final 2 modules

  const height = 40;
  let x = 0;
  let d = "";
  let i = 0;
  while (i < bits.length) {
    if (bits[i] === "1") {
      let run = 1;
      while (i + run < bits.length && bits[i + run] === "1") run++;
      const w = run * barWidth;
      d += `M${x} 0 h${w} v${height} h-${w} Z `;
      x += w;
      i += run;
    } else {
      let run = 1;
      while (i + run < bits.length && bits[i + run] === "0") run++;
      x += run * barWidth;
      i += run;
    }
  }
  return { path: d.trim(), width: x, height };
}

export function barcodeSvg(value: string): string {
  const { path, width, height } = code128SvgPath(value, 2);
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none"><path d="${path}" fill="#000"/></svg>`;
}
