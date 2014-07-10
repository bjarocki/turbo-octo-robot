#from pyvirtualdisplay import Display
import requests, uuid, json, boto, sys, StringIO, time
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
from selenium import webdriver
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities


# Support for sending room messages
class Hipchat:
    def __init__(self, configuration):
        self.room_id = configuration['room_id']
        self.auth_token = configuration['auth_token']
        self.message_from = configuration['message_from']
        self.notify = configuration['notify']
        self.format = configuration['message_format']

    def send(self, message, color):
        payload={
            "notify": self.notify,
            "message": message,
            "message_format": self.format,
            "from": self.message_from,
            "room_id": self.room_id,
            "color": color
        }
        headerdata={"content-type":"application/json"}
        r=requests.post("https://api.hipchat.com/v1/rooms/message?auth_token={0}".format(self.auth_token), params=payload, headers=headerdata)

# this class provides simple phantomjs/selenium tasks support
class DataDogTests:
    def __init__(self):
        self.window_size = (1400, 1000)
        #self._setup_display()
        self.user_agent = (
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_8_4) ' +
            'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/29.0.1547.57 Safari/537.36'
        )
        self.dcap = dict(DesiredCapabilities.PHANTOMJS)
        self.dcap["phantomjs.page.settings.userAgent"] = self.user_agent
        self.driver = webdriver.PhantomJS(desired_capabilities=self.dcap)
        #self.driver = webdriver.Firefox()
        self.driver.set_window_size(self.window_size[0], self.window_size[1])
        self.screenshots = []

    def _setup_display(self):
        self.display = Display(visible=0, size=self.window_size)
        self.display.start()

    def _measure_time(self, event_id, child_of=None):
        if not child_of and event_id not in self.measure:
            self.measure[event_id] = {'main': [], 'steps': {}}
            self.measure[event_id]['main'].append(time.time())
        else:
            if child_of:
                if event_id not in self.measure[child_of]['steps']:
                    self.measure[child_of]['steps'][event_id] = [time.time()]
                else:
                    self.measure[child_of]['steps'][event_id].append(time.time())
            else:
                self.measure[event_id]['main'].append(time.time())


    def go(self, flow):
        self.measure = {}
        for task in flow:
            self._measure_time(task['title'])

            if 'url' in task:
                self.driver.get(task['url'])

            for step in task['steps']:
                if 'measure' in step and step['measure'] == 'time':
                    self._measure_time(step['title'], child_of=task['title'])

                if step['action'] == 'click':
                    self.driver.find_element_by_xpath(step['on']).click()
                elif step['action'] == 'clear':
                    self.driver.find_element_by_xpath(step['on']).clear()
                elif step['action'] == 'send_keys':
                    self.driver.find_element_by_xpath(step['on']).send_keys(step['what'])
                elif step['action'] == 'screenshot':
                    self.screenshots.append((self.driver.get_screenshot_as_png(), {'title': step['title'], 'task_id': task['title']}))

                if 'measure' in step and step['measure'] == 'time':
                    self._measure_time(step['title'], child_of=task['title'])

            self._measure_time(task['title'])

        # show times for different actions - this can easily go to datadog
        print(json.dumps(self.measure, indent=4, sort_keys=True))

        return (self.screenshots, self.measure)

    def quit(self):
        #self.display.stop()
        self.driver.quit()

# Generate thumbnail from images tuples (image_data, description text)
class DataDogPreview:
    def __init__(self, images, measure_results, bucket):
        try:
            self.s3_bucket = bucket
            self.s3_base_url = 'http://dd-deployment-snapshots.s3-website-us-east-1.amazonaws.com/'
            self.file_name = '{0}.png'.format(str(uuid.uuid4()))
            self.preview_max_size = (700, 300)
            self.preview_format = 'PNG'
            self.s3_content_type = 'image/png'
            self.images = images
            self.measure_results = measure_results
            self.font_filename = 'Avenir.ttc'
            self.font_h1_size = 14
            self.font_h2_size = 10

            self._load_images()
        except Exception as e:
            raise

    def _text_on_image(self, image, text):
        start_from = (self.preview_max_size[1] - 25 - ((len(text) - 1) * 16))
        draw = ImageDraw.Draw(image)
        font_h1 = ImageFont.truetype(self.font_filename, self.font_h1_size)
        font_h2 = ImageFont.truetype(self.font_filename, self.font_h2_size)
        draw.text((5, start_from), text[0], (0,0,0), font=font_h1)
        text.pop(0)
        start_from += 20
        for row in text:
            draw.text((5, start_from), ' - {0}'.format(row), (0,0,0), font=font_h2)
            start_from += 14

    def _prepare_text(self, task_id, info):
        text = []
        # is there anything we should put on the screenshot?
        if task_id in measure_results:
            text.append('{0}: {1:2.4}s'.format(info['title'], (self.measure_results[task_id]['main'][1] - measure_results[task_id]['main'][0])))
            if 'steps' in self.measure_results[task_id] and len(self.measure_results[task_id]['steps']):
                for title, times in self.measure_results[task_id]['steps'].items():
                    text.append('{0}: {1:2.4}s'.format(title, (times[1] - times[0])))
        else:
            text.append('{0}'.format(info['title']))

        return text


    def _load_images(self):
        # prepare empty transparent preview image
        self.preview = Image.new('RGB', self.preview_max_size, (255, 255, 255))
        self.preview.putalpha(255)
        image_no = 0

        for image in self.images:
            (image_data, info) = image
            input_io = StringIO.StringIO(image_data)
            im = Image.open(input_io)
            im.thumbnail((int(self.preview_max_size[0] / float(len(self.images))), im.size[1]))

            # Check if we need to crop and fade out
            if im.size[1] >= self.preview_max_size[1]:
                im = im.crop((0,0, im.size[0], self.preview_max_size[1]))
                self._fade_out(im)
            else:
                transparent = Image.new('RGB', (im.size[0], self.preview_max_size[1]), (255, 255, 255))
                transparent.putalpha(0)
                transparent.paste(im, (0, 0))
                im = transparent

            self._text_on_image(im, self._prepare_text(info['task_id'], info))
            self.preview.paste(im, (im.size[0] * image_no, 0))
            image_no += 1

    def _fade_out(self, image):
        pixels = image.load()
        width, height = image.size
        for y in range(int(height*.85), int(height)):
            alpha = 255-int((y - height*.85)/height/.15 * 255)
            for x in range(width):
                pixels[x, y] = pixels[x, y][:3] + (alpha,)

    # this should be somewhere else but it's fine for now
    def upload(self):
        output = StringIO.StringIO()
        self.preview.save(output, format=p.preview_format, optimize=True)
        k = self.s3_bucket.new_key(self.file_name)
        k.set_metadata('Content-Type', 'image/png')
        k.set_contents_from_string(output.getvalue(), policy='public-read')

        return '{0}{1}'.format(self.s3_base_url, self.file_name)


if __name__ == '__main__':
    try:

        with open(sys.argv[1]) as f:
            configuration = json.loads(f.read())

        s3 = boto.connect_s3(configuration['s3']['aws_access_key_id'], configuration['s3']['aws_secret_access_key'])
        b = s3.get_bucket(configuration['s3']['bucket_name'])

        t = DataDogTests()
        screenshots, measure_results = t.go(configuration['workflow'])
        t.quit()

        if not len(screenshots):
            sys.exit(1)

        p = DataDogPreview(screenshots, measure_results, b)
        url = p.upload()
        h = Hipchat(configuration['hipchat'])
        h.send("<img src='{0}'>".format(url), 'green')
        #h.send("<a href='{0}'>More details about this jenkins job</a>".format('href="http://bastion.datad0g.com:8080/job/deploy-dogweb-hotfix/1737/'), 'green')

    except Exception as e:
        print(str(e))
        sys.exit(1)

