from PIL import Image, ImageDraw

S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
# rounded background
d.rounded_rectangle((8, 8, S-8, S-8), radius=56, fill=(13, 16, 22, 255), outline=(36, 43, 55, 255), width=3)
# subtle top highlight
d.rounded_rectangle((30, 28, S-30, 60), radius=16, fill=(28, 34, 45, 255))
# battery body
bx1, by1, bx2, by2 = 40, 92, 202, 172
d.rounded_rectangle((bx1, by1, bx2, by2), radius=14, fill=(22, 28, 38, 255), outline=(120, 134, 156, 255), width=6)
# terminal
d.rounded_rectangle((bx2+4, 116, bx2+18, 148), radius=5, fill=(120, 134, 156, 255))
# charge fill
d.rounded_rectangle((bx1+10, by1+10, bx1+10+int((bx2-bx1-20)*0.82), by2-10), radius=7, fill=(74, 222, 128, 255))
# bolt
bolt = [(136, 70), (108, 132), (132, 132), (118, 182), (158, 116), (132, 116)]
d.polygon(bolt, fill=(255, 255, 255, 255))
img.save("app.ico", sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
img.save("app_icon_preview.png")
print("ok")
