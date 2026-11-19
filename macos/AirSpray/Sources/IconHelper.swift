import Cocoa

public struct IconHelper {
    private static let activeSVGString = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="100%" height="100%">
  <!-- Base Bowl (Always Solid Filled) -->
  <path d="M 90,296 C 167,308 345,308 422,296 C 422,370 360,415 256,415 C 152,415 90,370 90,296 Z" fill="#000000" />

  <!-- Top Dome (Solid Filled with Transparent Cutout Nozzle) -->
  <path
    d="M 90,280 C 90,150 130,95 205,92 C 256,90 256,90 307,92 C 382,95 422,150 422,280 C 345,292 167,292 90,280 Z M 220,105 A 36,9 0 1,0 292,105 A 36,9 0 1,0 220,105 Z"
    fill="#000000" fill-rule="evenodd" />
</svg>
"""

    private static let standbySVGString = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="100%" height="100%">
  <g opacity="0.5">
    <!-- Base Bowl (Always Solid Filled) -->
    <path d="M 90,296 C 167,308 345,308 422,296 C 422,370 360,415 256,415 C 152,415 90,370 90,296 Z" fill="#000000" />

    <!-- Top Dome (Real SVG Stroke Outline with Hollow Interior) -->
    <path
      d="M 96,280 C 96,155 133,101 206,98 C 256,96 256,96 306,98 C 379,101 416,155 416,280 C 342,290 170,290 96,280 Z"
      fill="none" stroke="#000000" stroke-width="8" stroke-linejoin="round" />

    <!-- Top Nozzle Circle (Filled at 50% Opacity) -->
    <ellipse cx="256" cy="105" rx="36" ry="9" fill="#000000" />
  </g>
</svg>
"""

    public static func makeMenuBarIcon(isPoweredOn: Bool) -> NSImage {
        let svgStr = isPoweredOn ? activeSVGString : standbySVGString
        if let data = svgStr.data(using: .utf8), let img = NSImage(data: data) {
            img.size = NSSize(width: 22, height: 22)
            img.isTemplate = true
            return img
        }
        return NSImage()
    }
}
