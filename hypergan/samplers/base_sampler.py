import numpy as np
from PIL import Image
from hypergan.samplers.viewer import GlobalViewer

class BaseSampler:
    def __init__(self, gan, samples_per_row=8, session=None):
        #TODO USE THE TEST SESSION
        self.gan = gan
        self.samples_per_row = samples_per_row

    def _sample(self):
        raise "raw _sample method called.  You must override this"

    def sample(self, path):
        gan = self.gan
        if not gan.created:
            print("Creating sampler")
            self.gan.create()

        with gan.session.as_default():

            sample = self._sample()

            data = sample['generator'] #TODO variable

            width = min(gan.batch_size(), self.samples_per_row)
            stacks = [np.hstack(data[i*width:i*width+width]) for i in range(gan.batch_size()//width)]
            sample_data = np.vstack(stacks)
            self.plot(sample_data, path)
            sample_name = 'generator'
            samples = [[sample_data, sample_name]]

            return [{'image':sample_data, 'label':'sample'} for sample_data, sample_filename in samples] #TODO

    def plot(self, image, filename):
        """ Plot an image."""
        image = np.minimum(image, 1)
        image = np.maximum(image, -1)
        image = np.squeeze(image)
        # Scale to 0..255.
        imin, imax = image.min(), image.max()
        image = (image - imin) * 255. / (imax - imin) + .5
        image = image.astype(np.uint8)
        Image.fromarray(image).save(filename)
        GlobalViewer.update(image)
