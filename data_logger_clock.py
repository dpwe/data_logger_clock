# lcd_clock.py
#
# The Arduino lcd_clock reimplemented in CircuitPython.
#
# 2022-04-25 dpwe@

import array
import math
import time
import gc

import microcontroller  # for reboot()
import board
import digitalio
import busio
import adafruit_ds3231
import adafruit_bme680
import displayio
import terminalio
from adafruit_display_text import label
from adafruit_display_shapes.rect import Rect
from adafruit_display_shapes.line import Line
import adafruit_bitbangio as bitbangio
from adafruit_bitmap_font import bitmap_font
from adafruit_debouncer import Debouncer
import adafruit_displayio_sh1107
import adafruit_ssd1322

i2c = board.I2C() #frequency=400000)
#i2c = bitbangio.I2C(board.SCL, board.SDA, timeout = 1000)

sensor = adafruit_bme680.Adafruit_BME680_I2C(i2c)

def temp_c_to_f(temp_c):
    return 1.8*temp_c + 32.0

rtc = adafruit_ds3231.DS3231(i2c)

#DISPLAY = "SH1107"
DISPLAY = "SSD1322"

displayio.release_displays()
# oled_reset = board.D9  # D9 now used for PIR input

if DISPLAY == "SH1107":

  # Use for I2C
  #i2c = board.I2C()
  display_bus = displayio.I2CDisplay(i2c, device_address=0x3C)

  # SH1107 is vertically oriented 64x128
  WIDTH = 128
  HEIGHT = 64
  BORDER = 2

  display = adafruit_displayio_sh1107.SH1107(
      display_bus, width=WIDTH, height=HEIGHT, rotation=0,
      auto_refresh=False
  )

elif DISPLAY == "SSD1322":

  # This pinout works on a Metro and may need to be altered for other boards.
  spi = busio.SPI(board.D10, board.D11)
  tft_cs = board.D13
  tft_dc = board.D12
  tft_reset = None  # board.D5

  display_bus = displayio.FourWire(
      spi, command=tft_dc, chip_select=tft_cs, reset=tft_reset, baudrate=1000000
  )
  time.sleep(1)
  display = adafruit_ssd1322.SSD1322(display_bus, width=256, height=64, colstart=112,
        auto_refresh=False)


dayname = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

def day_of_century(year, month, day):
    """ 2000-01-01 is 0."""
    days = 365 * (year - 2000) + max(0, year - 1997) // 4
    days += sum(days_in_month[:(month - 1)])
    if (year % 4) == 0 and month > 2:
        days += 1  # Leap day
    days += day - 1  # First day of month is numbered 1
    return days

def day_of_week(year, month, day):
    """0 = Sunday."""
    # 2000-01-01 was a Saturday (6)
    return (6 + day_of_century(year, month, day)) % 7

def first_sunday(year, month):
    """Return day-of-month of first Sunday in specified month."""
    weekday_of_first_day = day_of_week(year, month, 1)
    return 1 + ((7 - weekday_of_first_day) % 7)

def is_dst(secs_in_utc):
  """Convert tm_struct to boolean if DST is in effect."""
  year = (time.localtime(secs_in_utc)).tm_year
  # DST begins 2am on second Sunday in March
  HHMarch = time.mktime((year, 3 , 7 + first_sunday(year, 3), 2, 0, 0, 0, 0, 0))
  # DST ends 2am on first Sunday in Novembner
  HHNovember = time.mktime((year, 11 , first_sunday(year, 11), 2, 0, 0, 0, 0, 0))
  if secs_in_utc >= HHMarch and secs_in_utc < HHNovember:
    return True
  return False

TZ_HOURS = -5

def my_localtime(secs_in_utc):
  """Convert utc_secs to a time struct including DST."""
  t = time.localtime(secs_in_utc + 3600 * TZ_HOURS)
  if is_dst(secs_in_utc):
    t = time.localtime(secs_in_utc + 3600 * (TZ_HOURS + 1))
  return t

def format_time(secs_in_utc):
  t = my_localtime(secs_in_utc)
  return '{:4d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}'.format(t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec)

DO_LOG = True
def log(msg):
    if DO_LOG:
        print(format_time(time.mktime(rtc.datetime)), ":", msg)

class TimeDisplay(object):
  def __init__(self, left_x=0, top_y=0):
    self.date_label = label.Label(terminalio.FONT, text="Wed 2022-05-18", x=left_x + 4, y=top_y + 4)
    big_font = bitmap_font.load_font("fonts/CalBlk36.pcf")
    self.hour_label = label.Label(big_font, text="22", anchored_position=(left_x + 39, top_y + 28), anchor_point=(1.0, 0.5))
    self.colon_label = label.Label(big_font, text=":", anchored_position=(left_x + 44, top_y + 28), anchor_point=(0.5, 0.5))
    self.min_label = label.Label(big_font, text="22", anchored_position=(left_x + 50, top_y + 28), anchor_point=(0.0, 0.5))
    # Memory display
    tiny_font = bitmap_font.load_font("fonts/tom-thumb.pcf")
    self.mem_label = label.Label(tiny_font, text="999,999 B free", x=left_x, y=top_y + 59)
    # Make the display context
    self.time_disp = displayio.Group()
    # Draw some label text
    self.time_disp.append(self.date_label)
    self.time_disp.append(self.hour_label)
    self.time_disp.append(self.min_label)
    self.time_disp.append(self.colon_label)
    self.time_disp.append(self.mem_label)

    self.sec_total_w = 60
    self.sec_left_x = left_x + 15
    self.sec_top_y = top_y + 44
    sec_frame = Rect(self.sec_left_x, self.sec_top_y, self.sec_total_w + 4, 8, outline=0xFFFFFF)
    self.sec_fill = Rect(self.sec_left_x + 2, self.sec_top_y + 2, self.sec_total_w, 4, fill=0xFFFFFF)
    self.time_disp.append(sec_frame)
    self.time_disp.append(self.sec_fill)

  def display_group(self):
    return self.time_disp

  def update_time_display(self, secs_in_utc):
    t = my_localtime(secs_in_utc)
    #my_label.text = '{:04}-{:02}-{:02} {:02}:{:02}:{:02}'.format(t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec)
    wday = day_of_week(t.tm_year, t.tm_mon, t.tm_mday)
    self.date_label.text = '{:s} {:04}-{:02}-{:02}'.format(dayname[wday], t.tm_year, t.tm_mon, t.tm_mday)
    self.hour_label.text = '{:02}'.format(t.tm_hour)
    self.min_label.text = '{:02}'.format(t.tm_min)
    if t.tm_sec & 1:
      self.colon_label.text = ' '
    else:
      self.colon_label.text = ':'
    bar_secs = 1 + ((t.tm_sec + 59) % 60)  # 0 reads as 60
    sec_mid_x = int(round(self.sec_total_w * bar_secs / 60))
    if ((secs - 1) // 60) & 1:
        sec_x = sec_mid_x
        sec_w = self.sec_total_w - sec_mid_x
    else:
        sec_x = 0
        sec_w = sec_mid_x
    # sec_fill was the last item added to the time_disp group.  Delete and replace.
    del self.time_disp[-1]
    self.sec_fill = Rect(self.sec_left_x + 2 + sec_x, self.sec_top_y + 2, sec_w, 4, fill=0xFFFFFF)
    self.time_disp.append(self.sec_fill)
    # Memory display
    gc.collect()
    mem_free = gc.mem_free()
    self.mem_label.text = "{:d},{:03d} B free".format(mem_free // 1000, mem_free % 1000)


class LogData(object):
  """Collected regularly-spaced logging data."""
  def __init__(self, fields, interval_secs, max_len=120, filename=None):
    self.fields = fields
    self.interval_secs = interval_secs
    self.data = []  # array.array('f')
    self.times = array.array('l')
    self.max_len = max_len
    self.registered_displays = []
    self.filename = filename
    self.unsaved_lines = 0
    if self.filename:
      self.load(self.filename)

  def load(self, filename):
    """Read-in previously-saved data, if any."""
    num_lines = 0
    try:
      with open(filename, "r") as fp:
        for line in fp:
            num_lines += 1
      lines_read = 0
      with open(filename, "r") as fp:
        for line in fp:
          if lines_read >= num_lines - self.max_len:
            fields = [s.strip() for s in line.strip().split(',')]
            # Make sure time is stored as a long int.
            self.times.append(int(fields[0]))
            self.data.append([float(s) for s in fields[1:]])
          lines_read += 1
    except OSError as e:  # e.g. file not found
      log("Cannot read " + filename)
    log(str(num_lines) + " lines read from " + filename)
    # Restrict total storage
    self.times = self.times[-self.max_len:]
    self.data = self.data[-self.max_len:]
    self.unsaved_lines = 0

  def save(self, filename):
    """Attempt to write the new data so far to file."""
    num_lines_added = 0
    if not filename:
      return
    try:
      first_datum_index = len(self.times) - self.unsaved_lines
      log("starting from datum " + str(first_datum_index))
      with open(filename, "a") as fp:
        for data_index in range(first_datum_index, len(self.times)):
          fp.write('{:s}\n'.format(','.join(str(x) for x in [self.times[data_index]] + self.data[data_index])))
          fp.flush()
          self.unsaved_lines -= 1
          num_lines_added += 1
    except OSError as e:  # Typically when the filesystem isn't writeable...
      log("Cannot write " + filename)
    log(str(num_lines_added) + " lines added to " + filename)

  def time_to_log(self, time_secs):
    # If we submitted a datum at this time, would it be logged?
    if len(self.times):
      last_time_step = self.times[-1] // self.interval_secs
    else:
      last_time_step = -1
    new_time_step = time_secs // self.interval_secs
    return new_time_step != last_time_step

  def log_data(self, values, time_secs):
    if self.time_to_log(time_secs):
      # We have new data to log.
      self.data.append(values)
      self.times.append(time_secs)
      # Drop earliest values if we have too many.
      #self.data = self.data[-self.max_len:]
      #self.times = self.times[-self.max_len:]
      # Update dependent displays
      self.update_displays()
      # Maybe save to disk.
      self.save(self.filename)
      self.unsaved_lines += 1

  def update_displays(self):
      for data_display in self.registered_displays:
        data_display.display_log()

  def fetch_data(self, channel):
    channel_data = []
    for i in range(len(self.data)):
      channel_data.append(self.data[i][channel])
    return self.times, channel_data

  def register_display(self, data_display):
    self.registered_displays.append(data_display)


def paste_bitmap(glyph, x, y, bitmap, color):
  """Copy a glyph directly onto a bitmap."""
  for g_x in range(glyph.width):
    for g_y in range(glyph.height):
      if glyph.bitmap[g_x, g_y] and ((x + g_x) < bitmap.width) and ((y + g_y) < bitmap.height):
        bitmap[x + g_x, y + g_y] = color


def print_on_bitmap(bitmap, x, y, text, font, color):
  """Directly render a font onto a bitmap."""
  for c in text:
    glyph = font.get_glyph(ord(c))
    paste_bitmap(glyph, x, y, bitmap, color)
    x += glyph.width + 1

TER_FONT = bitmap_font.load_font("fonts/ter-u12n.pcf", displayio.Bitmap)
TTH_FONT = bitmap_font.load_font("fonts/tom-thumb.pcf", displayio.Bitmap)

class DataDisplay(object):
    """Plot the results of one sequence."""
    def __init__(self, x, y, w, h, logger, channel,
                 secs_per_pixel=12 * 60, secs_per_legend=6 * 60 * 60, legend_parity=0,
                 show_time_legend=False, units='', signficant_figures=3):
        self.x = x
        self.y = y
        self.w = w
        self.h = h
        self.logger = logger
        logger.register_display(self)
        self.channel = channel
        self.secs_per_pixel = secs_per_pixel
        self.secs_per_legend = secs_per_legend
        self.legend_parity = legend_parity
        self.show_time_legend = show_time_legend
        self.units = units
        self.significant_figures = signficant_figures
        self.num_data = logger.max_len
        self.legend_w = 8
        # Bitmap for line display
        self.plot_w = w - self.legend_w
        self.bitmap = displayio.Bitmap(self.plot_w, h, 3)
        self.palette = displayio.Palette(3)
        self.palette[0] = 0x000000
        self.palette[1] = 0x888888
        self.palette[2] = 0xFFFFFF
        self.tile_grid = displayio.TileGrid(bitmap=self.bitmap, pixel_shader=self.palette, width=1, height=1,
                           tile_width=self.plot_w, tile_height=h, default_tile=0, x=self.x + self.legend_w, y=self.y)
        self.disp_group = displayio.Group()
        self.disp_group.append(self.tile_grid)
        # Legends
        self.plot_x = x + self.legend_w
        self.tiny_font = TTH_FONT
        self.max_label = label.Label(self.tiny_font, text="123", anchored_position=(x, y), anchor_point=(0.0, 0.0))
        self.min_label = label.Label(self.tiny_font, text="123", anchored_position=(x, y + h), anchor_point=(0.0, 1.0))
        self.val_label = label.Label(TER_FONT, text="1234" + units, anchored_position=(x + w + 1, y + h // 2), anchor_point=(0.0, 0.5))
        name_label = label.Label(self.tiny_font, text=logger.fields[channel], anchored_position=(x, y + h//2), anchor_point=(0.0, 0.5))
        self.disp_group.append(self.min_label)
        self.disp_group.append(self.max_label)
        self.disp_group.append(name_label)
        self.disp_group.append(self.val_label)

    def display_group(self):
        return self.disp_group

    def display(self, times, data):
        """Draw a trace with the provided data."""
        # We only plot the num_data most recent items.
        data = data[-self.num_data:]
        times = times[-self.num_data:]
        data_min = math.floor(min(data))
        data_max = math.ceil(max(data))
        data_min = min(data_min, data_max - 1.0)
        data_max = max(data_min + 1.0, data_max)
        data_range = data_max - data_min
        data_len = len(data)
        latest_time = times[-1]
        pixels_per_legend = self.secs_per_legend // self.secs_per_pixel
        # Fill the background.
        for xx in range(self.plot_w):
          x = self.plot_w - 1 - xx
          local_time = latest_time + 3600 * TZ_HOURS + (x - self.plot_w - 1) * self.secs_per_pixel
          local_time_in_pixels = local_time // self.secs_per_pixel
          bg_pixel = ((local_time_in_pixels // pixels_per_legend) + self.legend_parity) % 2
          pixel_within_legend = local_time_in_pixels % pixels_per_legend
          if pixel_within_legend == 0:
            earliest_legend_x = x
          for y in range(self.h):
            self.bitmap[x, y] = bg_pixel
        # Add time legends
        #earliest_legend_x = (pixels_per_legend - (local_time_in_pixels % pixels_per_legend)) % pixels_per_legend
        if self.show_time_legend:
          for i, legend_x in enumerate(range(earliest_legend_x, self.plot_w, pixels_per_legend)):
            text = '{:02d}'.format((((local_time_in_pixels + legend_x) * self.secs_per_pixel) // 3600) % 24)
            print_on_bitmap(self.bitmap, legend_x + 1, 0, text, self.tiny_font, (self.legend_parity + i + 1)%2)
        # Draw the trace.
        for index in range(data_len):
            datum = data[-(index + 1)]
            time = times[-(index + 1)]
            #data_x = self.plot_w - 1 - round(index * self.plot_w / self.num_data)
            data_x = self.plot_w - 1 - ((latest_time - time) // self.secs_per_pixel)
            if data_x >= 0:
              # Fill with 1 if it's a legend line pixel
              local_time_in_pixels = (time + 3600 * TZ_HOURS) // self.secs_per_pixel
              #bg_pixel = ((local_time_in_pixels // pixels_per_legend) + self.legend_parity) % 2
              #for data_y in range(self.h):
              #  self.bitmap[data_x, data_y] = bg_pixel
              data_y = round((self.h - 1) * (1.0 - (datum - data_min) / data_range))
              self.bitmap[data_x, data_y] = 2
              # Update value legend
        self.min_label.text = '{:3.0f}'.format(data_min)
        self.max_label.text = '{:3.0f}'.format(data_max)
        self.val_label.text = ('{:.' + str(self.significant_figures) + 'g}').format(data[-1]) + self.units

    def display_log(self):
        self.display(*self.logger.fetch_data(self.channel))


############## initialize ###############
log("data_logger_clock")

data_log = LogData(["°F", "%H", "Pa", "Go"], interval_secs=60 * 12, max_len=120, filename="data.csv")

time_disp = TimeDisplay(left_x=2)
disp_left = 97
temp_disp = DataDisplay(disp_left, 0, 128, 15, logger=data_log, channel=0, show_time_legend=True, units='°')
humi_disp = DataDisplay(disp_left, 16, 128, 15, logger=data_log, channel=1, legend_parity=1, units='%')
pres_disp = DataDisplay(disp_left, 32, 128, 15, logger=data_log, channel=2, units='Pa', signficant_figures=4)
gaso_disp = DataDisplay(disp_left, 48, 128, 15, logger=data_log, channel=3, legend_parity=1, units='Ω', signficant_figures=4)
data_log.update_displays()

master_group = displayio.Group()
master_group.append(time_disp.display_group())
master_group.append(temp_disp.display_group())
master_group.append(humi_disp.display_group())
master_group.append(pres_disp.display_group())
master_group.append(gaso_disp.display_group())

display.show(master_group)

# For blanking
blank_group = displayio.Group()

class SideScrollBitmap(object):
    """Class to manage a flat bitmap with side-scrolling."""
    def __init__(self, x, y, width, height):
        self.width = width
        self.height = height
        self.bitmap = displayio.Bitmap(width, height, 2)
        self.palette = displayio.Palette(2)
        self.palette[0] = 0x000000
        self.palette[1] = 0xFFFFFF
        self.tile_grid = displayio.TileGrid(bitmap=self.bitmap, pixel_shader=self.palette, width=width, height=1,
                           tile_width=1, tile_height=height, default_tile=0, x=x, y=y)
        self.display_at(0)

    def display_at(self, origin=0):
        self.origin = origin
        for tile in range(self.width):
            self.tile_grid[tile] = (origin + tile) % self.width

    def set_rh_pixel(self, y, val=1):
        x = (self.origin - 1 + self.width) % self.width
        self.bitmap[x, y] = val

    def scroll_left(self, steps=1):
        self.display_at(self.origin + 1)
        # Clear the RH edge.
        x = (self.origin - 1 + self.width) % self.width
        for y in range(self.height):
            self.bitmap[x, y] = 0


#scroller = SideScrollBitmap(128, 0, 128, 64)
#master_group.append(scroller.tile_grid)

# Button to toggle display
display_on = True
display_was_on = False
button = digitalio.DigitalInOut(board.D6)
button.direction = digitalio.Direction.INPUT
button.pull = digitalio.Pull.UP
debounced_button = Debouncer(button)

# PIR sensor feeds D9
pir_sensor = digitalio.DigitalInOut(board.D9)
pir_sensor.direction = digitalio.Direction.INPUT
pir_was_active = False

last_secs = 0
last_min = 0
first_min_since_reset = True
# Don't reset on a save-data minute, wait until 00:01.
reset_hour = 0
reset_min = 1
# Timer for screen dim
last_action_secs = time.mktime(rtc.datetime)
# How long until screen blanks?
screensaver_secs = 300

while True:
    secs = time.mktime(rtc.datetime)
    if secs != last_secs:
        last_secs = secs
        if data_log.time_to_log(secs):
            data_log.log_data([temp_c_to_f(sensor.temperature), sensor.humidity, sensor.pressure, sensor.gas], secs)  # / 1000
        #scroller.set_rh_pixel(int(round((temp_c_to_f(sensor.temperature) - 70))))
        #scroller.scroll_left()
        if display_on:
            if secs > last_action_secs + screensaver_secs:
                # System is idle, blank the screen.
                display_on = False
                log("screensaver timeout")
            else:
                time_disp.update_time_display(secs)
                display.show(master_group)
                display.refresh()
                display_was_on = True
        else:
            if display_was_on:
                display.show(blank_group)
                display.refresh()
                display_was_on = False
        #gc.collect()
        # Reset every hour at 0:01
        t = my_localtime(secs)
        if t.tm_min != last_min:
            last_min = t.tm_min
            if first_min_since_reset:
                # Don't do anything special during the first minute after reset.
                first_min_since_reset = False
            else:
                # Check if it's time to reset
                if t.tm_hour == reset_hour and t.tm_min == reset_min:
                    microcontroller.reset()
    # Check button
    debounced_button.update()
    if debounced_button.rose:
        display_on = not display_on
        # Reset screensaver timer.
        last_action_secs = secs
        log("button rose; display=" + str(display_on))
    if pir_sensor.value is not pir_was_active:
        pir_was_active = pir_sensor.value
        if pir_sensor.value:
            display_on = True
            # Reset screensaver timer.
            last_action_secs = secs
        log("pir=" + str(pir_sensor.value) + " display=" + str(display_on))
    time.sleep(0.02)
    