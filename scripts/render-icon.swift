import AppKit
let size = 1024
let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
NSColor(srgbRed: 0.125, green: 0.337, blue: 0.298, alpha: 1).setFill()
NSBezierPath(rect: NSRect(x: 0, y: 0, width: size, height: size)).fill()
let frame = NSBezierPath(roundedRect: NSRect(x: 292, y: 194, width: 440, height: 636), xRadius: 35, yRadius: 35)
NSColor(srgbRed: 0.95, green: 0.95, blue: 0.88, alpha: 1).setStroke()
frame.lineWidth = 37
frame.stroke()
let door = NSBezierPath()
door.move(to: NSPoint(x: 314, y: 210))
door.line(to: NSPoint(x: 564, y: 282))
door.line(to: NSPoint(x: 564, y: 759))
door.line(to: NSPoint(x: 314, y: 809))
door.close()
NSColor(srgbRed: 0.95, green: 0.95, blue: 0.88, alpha: 1).setFill()
door.fill()
NSColor(srgbRed: 0.8, green: 0.36, blue: 0.23, alpha: 1).setFill()
NSBezierPath(ovalIn: NSRect(x: 498, y: 493, width: 35, height: 35)).fill()
NSGraphicsContext.restoreGraphicsState()
// App Store icons must not carry an alpha channel, even when every pixel is opaque.
let rgb = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8, samplesPerPixel: 3, hasAlpha: false, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: size * 3, bitsPerPixel: 24)!
let source = bitmap.bitmapData!, target = rgb.bitmapData!
for y in 0..<size {
    for x in 0..<size {
        for component in 0..<3 {
            target[y * rgb.bytesPerRow + x * 3 + component] = source[y * bitmap.bytesPerRow + x * 4 + component]
        }
    }
}
try rgb.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
