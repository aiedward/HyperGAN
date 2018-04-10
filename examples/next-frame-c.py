import os
import uuid
import random
import tensorflow as tf
import hypergan as hg
import hyperchamber as hc
import numpy as np
import glob
from hypergan.viewer import GlobalViewer
from hypergan.samplers.base_sampler import BaseSampler
from hypergan.gan_component import ValidationException, GANComponent
from hypergan.samplers.random_walk_sampler import RandomWalkSampler
from hypergan.samplers.debug_sampler import DebugSampler
from hypergan.search.alphagan_random_search import AlphaGANRandomSearch
from hypergan.gans.base_gan import BaseGAN
from common import *

import copy

from hypergan.gans.alpha_gan import AlphaGAN

from hypergan.gan_component import ValidationException, GANComponent
from hypergan.gans.base_gan import BaseGAN

from hypergan.discriminators.fully_connected_discriminator import FullyConnectedDiscriminator
from hypergan.encoders.uniform_encoder import UniformEncoder
from hypergan.trainers.multi_step_trainer import MultiStepTrainer
from hypergan.trainers.multi_trainer_trainer import MultiTrainerTrainer
from hypergan.trainers.consensus_trainer import ConsensusTrainer


arg_parser = ArgumentParser("render next frame")
arg_parser.add_image_arguments()
args = arg_parser.parse_args()

width, height, channels = parse_size(args.size)

config = lookup_config(args)
if args.action == 'search':
    random_config = AlphaGANRandomSearch({}).random_config()
    if args.config_list is not None:
        config = random_config_from_list(args.config_list)

        config["generator"]=random_config["generator"]
        config["g_encoder"]=random_config["g_encoder"]
        config["discriminator"]=random_config["discriminator"]
        config["z_discriminator"]=random_config["z_discriminator"]

        # TODO Other search terms?
    else:
        config = random_config

class VideoFrameLoader:
    """
    """

    def __init__(self, batch_size):
        self.batch_size = batch_size

    def create(self, directory, channels=3, format='jpg', width=64, height=64, crop=False, resize=False):
        directories = glob.glob(directory+"/*")
        directories = [d for d in directories if os.path.isdir(d)]

        if(len(directories) == 0):
            directories = [directory] 

        # Create a queue that produces the filenames to read.
        if(len(directories) == 1):
            # No subdirectories, use all the images in the passed in path
            filenames = glob.glob(directory+"/*."+format)
        else:
            filenames = glob.glob(directory+"/**/*."+format)

        self.file_count = len(filenames)
        filenames = sorted(filenames)
        print("FILENAMES", filenames[:-2][0], filenames[1:-1][0], filenames[2:-2][0])
        if self.file_count == 0:
            raise ValidationException("No images found in '" + directory + "'")


        input_t = [filenames[:-2], filenames[1:-1], filenames[2:]]
        #input_t = [f1 + ',' + f2 + ',' + f3 for f1,f2,f3 in zip(*input_t)]
        #input_queue = tf.train.string_input_producer(input_t, shuffle=True)
        #x1,x2,x3 = tf.decode_csv(input_queue.dequeue(), [[""], [""], [""]], ",")
        input_queue = tf.train.slice_input_producer(input_t, shuffle=True)
        x1,x2,x3 = input_queue
        print('---',x1)

        # Read examples from files in the filename queue.
        x1 = self.read_frame(x1, format, crop, resize)
        x2 = self.read_frame(x2, format, crop, resize)
        x3 = self.read_frame(x3, format, crop, resize)
        x1,x2,x3 = self._get_data(x1,x2,x3)
        self.x1 = self.x = x1
        self.x2 = x2
        self.x3 = x3
        return [x1, x2, x3], None


    def read_frame(self, t, format, crop, resize):
        value = tf.read_file(t)

        if format == 'jpg':
            img = tf.image.decode_jpeg(value, channels=channels)
        elif format == 'png':
            img = tf.image.decode_png(value, channels=channels)
        else:
            print("[loader] Failed to load format", format)
        img = tf.cast(img, tf.float32)


      # Image processing for evaluation.
      # Crop the central [height, width] of the image.
        if crop:
            resized_image = hypergan.inputs.resize_image_patch.resize_image_with_crop_or_pad(img, height, width, dynamic_shape=True)
        elif resize:
            resized_image = tf.image.resize_images(img, [height, width], 1)
        else: 
            resized_image = img

        tf.Tensor.set_shape(resized_image, [height,width,channels])

        # This moves the image to a range of -1 to 1.
        float_image = resized_image / 127.5 - 1.

        return float_image

    def _get_data(self, x1,x2,x3):
        batch_size = self.batch_size
        num_preprocess_threads = 24
        x1,x2,x3 = tf.train.shuffle_batch(
            [x1,x2,x3],
            batch_size=batch_size,
            num_threads=num_preprocess_threads,
            capacity= batch_size*2, min_after_dequeue=batch_size)
        return x1,x2,x3
inputs = VideoFrameLoader(args.batch_size)
inputs.create(args.directory,
        channels=channels, 
        format=args.format,
        crop=args.crop,
        width=width,
        height=height,
        resize=True)

save_file = "save/model.ckpt"

class AliNextFrameGAN(BaseGAN):
    """ 
    """
    def __init__(self, *args, **kwargs):
        BaseGAN.__init__(self, *args, **kwargs)

    def required(self):
        """
        `input_encoder` is a discriminator.  It encodes X into Z
        `discriminator` is a standard discriminator.  It measures X, reconstruction of X, and G.
        `generator` produces two samples, input_encoder output and a known random distribution.
        """
        return "generator discriminator ".split()

    def create(self):
        config = self.config
        ops = self.ops

        with tf.device(self.device):
            #x_input = tf.identity(self.inputs.x, name='input')
            last_frame_1 = tf.identity(self.inputs.x1, name='x1_t')
            last_frame_2 = tf.identity(self.inputs.x2, name='x2_t')
            self.last_frame_1 = last_frame_1
            self.last_frame_2 = last_frame_2
            xa_input = tf.concat(values=[last_frame_1, last_frame_2], axis=3)#tf.identity(self.inputs.xa, name='xa_i')
            xb_input = tf.identity(self.inputs.x3, name='xb_i')

            if config.same_g:
                ga = self.create_component(config.generator, input=xb_input, name='a_generator')
                gb = hc.Config({"sample":ga.reuse(xa_input),"controls":{"z":ga.controls['z']}, "reuse": ga.reuse})
            else:
                hack_gen = config.generator
                hack_gen['layers'][-1] = "subpixel 6 avg_pool=1 activation=null"
                ga = self.create_component(hack_gen, input=xb_input, name='a_generator')
                hack_gen['layers'][-1] = "subpixel 3 avg_pool=1 activation=null"
                gb = self.create_component(hack_gen, input=xa_input, name='b_generator')

            za = ga.controls["z"]
            zb = gb.controls["z"]

            self.uniform_sample = ga.sample

            xba = ga.sample
            xab = gb.sample
            #xa_hat = ga.reuse(gb.sample)
            #xb_hat = gb.reuse(ga.sample)

            z_shape = self.ops.shape(za)
            uz_shape = z_shape
            uz_shape[-1] = uz_shape[-1] // len(config.z_distribution.projections)
            ue = UniformEncoder(self, config.z_distribution, output_shape=uz_shape)
            ue2 = UniformEncoder(self, config.z_distribution, output_shape=uz_shape)
            ue3 = UniformEncoder(self, config.z_distribution, output_shape=uz_shape)
            ue4 = UniformEncoder(self, config.z_distribution, output_shape=uz_shape)
            print('ue', ue.sample)

            zua = ue.sample
            zub = ue2.sample

            ue2 = UniformEncoder(self, config.z_distribution, output_shape=[self.ops.shape(zb)[0], config.source_linear])
            zub = ue2.sample
            uzb_to_gzb = self.create_component(config.uz_to_gz, name='uzb_to_gzb', input=zub)
            zub = uzb_to_gzb.sample
            sourcezub = zub

            ue2 = UniformEncoder(self, config.z_distribution, output_shape=[self.ops.shape(za)[0], config.source_linear])
            zua = ue2.sample
            uza_to_gza = self.create_component(config.uz_to_gz, name='uza_to_gza', input=zua)
            zua = uza_to_gza.sample
            sourcezua = zua
 
            ugb = gb.reuse(tf.zeros_like(xa_input), replace_controls={"z":zub})
            uga = ga.reuse(tf.zeros_like(xb_input), replace_controls={"z":zua})

            xa = xa_input
            xb = xb_input

            t0 = xb
            t1 = gb.sample
            t2 = ugb
            f0 = za
            f1 = zb
            f2 = zub
            stack = [t0, t1, t2]
            stacked = ops.concat(stack, axis=0)
            features = ops.concat([f0, f1, f2], axis=0)

            d = self.create_component(config.discriminator, name='d_b', input=stacked, features=[features])
            l = self.create_loss(config.loss, d, xa_input, ga.sample, len(stack))
            loss1 = l
            d_loss1 = l.d_loss
            g_loss1 = l.g_loss

            d_vars1 = d.variables()
            g_vars1 = gb.variables()+ga.variables()

            d_loss = l.d_loss
            g_loss = l.g_loss

            metrics = {}

            z_shape = self.ops.shape(za)
            uz_shape = z_shape
            uz_shape[-1] = uz_shape[-1] // len(config.z_distribution.projections)
            ueu = UniformEncoder(self, config.z_distribution, output_shape=uz_shape)
 
            noise = ueu.sample
            cb = self.create_component(config.z_generator, input=tf.concat(values=[zb, za, noise], axis=3), name='zb_generator')
            ca = self.create_component(config.z_generator, input=zb, name='za_generator')

            ub = cb.controls['u']
            ua = ca.controls['u']

            t0 = zb
            t1 = cb.sample
            f0 = ua
            f1 = ub
            stack = [t0, t1]
            stacked = ops.concat(stack, axis=0)
            features = ops.concat([f0, f1], axis=0)

            d2 = self.create_component(config.z_discriminator, name='zd_b', input=stacked, features=[features])
            l2 = self.create_loss(config.loss, d2, xa_input, ga.sample, len(stack))

            d_loss1 += l2.d_loss 
            g_loss1 += l2.g_loss 
            metrics['zlossd']=l2.d_loss
            metrics['zlossg']=l2.g_loss
            metrics['lossd']=l.d_loss
            metrics['lossg']=l.g_loss

            g_vars1 += cb.variables() + ca.variables()
            d_vars1 += d2.variables()

            ccontrols = gb.reuse(tf.zeros_like(xa_input), replace_controls={"u":ub})
 
            trainers = []

            lossa = hc.Config({'sample': [d_loss1, g_loss1], 'metrics': metrics})
            #lossb = hc.Config({'sample': [d_loss2, g_loss2], 'metrics': metrics})
            trainers += [ConsensusTrainer(self, config.trainer, loss = lossa, g_vars = g_vars1, d_vars = d_vars1)]
            #trainers += [ConsensusTrainer(self, config.trainer, loss = lossb, g_vars = g_vars2, d_vars = d_vars2)]
            trainer = MultiTrainerTrainer(trainers)
            self.session.run(tf.global_variables_initializer())

        self.trainer = trainer
        self.generator = ga
        self.encoder = hc.Config({"sample":ugb}) # this is the other gan
        self.uniform_encoder = hc.Config({"sample":zub})#uniform_encoder
        self.uniform_encoder_source = hc.Config({"sample":sourcezub})#uniform_encoder
        self.zb = zb
        self.z_hat = gb.sample
        self.x_input = xa_input

        self.c = ccontrols 

        self.xba = xba
        self.xab = xab
        self.uga = uga
        self.ugb = ugb

        rgb = tf.cast((self.generator.sample+1)*127.5, tf.int32)
        self.next_frame = gb.sample
        self.generator_int = tf.bitwise.bitwise_or(rgb, 0xFF000000, name='generator_int')


    def create_loss(self, loss_config, discriminator, x, generator, split):
        loss = self.create_component(loss_config, discriminator = discriminator, x=x, generator=generator, split=split)
        return loss

    def create_encoder(self, x_input, name='input_encoder'):
        config = self.config
        input_encoder = dict(config.input_encoder or config.g_encoder or config.generator)
        encoder = self.create_component(input_encoder, name=name, input=x_input)
        return encoder

    def create_z_discriminator(self, z, z_hat):
        config = self.config
        z_discriminator = dict(config.z_discriminator or config.discriminator)
        z_discriminator['layer_filter']=None
        net = tf.concat(axis=0, values=[z, z_hat])
        encoder_discriminator = self.create_component(z_discriminator, name='z_discriminator', input=net)
        return encoder_discriminator

    def create_cycloss(self, x_input, x_hat):
        config = self.config
        ops = self.ops
        distance = config.distance or ops.lookup('l1_distance')
        pe_layers = self.gan.skip_connections.get_array("progressive_enhancement")
        cycloss_lambda = config.cycloss_lambda
        if cycloss_lambda is None:
            cycloss_lambda = 10
        
        if(len(pe_layers) > 0):
            mask = self.progressive_growing_mask(len(pe_layers)//2+1)
            cycloss = tf.reduce_mean(distance(mask*x_input,mask*x_hat))

            cycloss *= mask
        else:
            cycloss = tf.reduce_mean(distance(x_input, x_hat))

        cycloss *= cycloss_lambda
        return cycloss


    def create_z_cycloss(self, z, x_hat, encoder, generator):
        config = self.config
        ops = self.ops
        total = None
        distance = config.distance or ops.lookup('l1_distance')
        if config.z_hat_lambda:
            z_hat_cycloss_lambda = config.z_hat_cycloss_lambda
            recode_z_hat = encoder.reuse(x_hat)
            z_hat_cycloss = tf.reduce_mean(distance(z_hat,recode_z_hat))
            z_hat_cycloss *= z_hat_cycloss_lambda
        if config.z_cycloss_lambda:
            recode_z = encoder.reuse(generator.reuse(z))
            z_cycloss = tf.reduce_mean(distance(z,recode_z))
            z_cycloss_lambda = config.z_cycloss_lambda
            if z_cycloss_lambda is None:
                z_cycloss_lambda = 0
            z_cycloss *= z_cycloss_lambda

        if config.z_hat_lambda and config.z_cycloss_lambda:
            total = z_cycloss + z_hat_cycloss
        elif config.z_cycloss_lambda:
            total = z_cycloss
        elif config.z_hat_lambda:
            total = z_hat_cycloss
        return total



    def input_nodes(self):
        "used in hypergan build"
        if hasattr(self.generator, 'mask_generator'):
            extras = [self.mask_generator.sample]
        else:
            extras = []
        return extras + [
                self.x_input
        ]


    def output_nodes(self):
        "used in hypergan build"

    
        if hasattr(self.generator, 'mask_generator'):
            extras = [
                self.mask_generator.sample, 
                self.generator.g1x,
                self.generator.g2x
            ]
        else:
            extras = []
        return extras + [
                self.encoder.sample,
                self.generator.sample, 
                self.uniform_sample,
                self.generator_int
        ]
class VideoFrameSampler(BaseSampler):
    def __init__(self, gan, samples_per_row=8):
        self.z = None


        self.i = 0
        BaseSampler.__init__(self, gan, samples_per_row)

    def _sample(self):
        gan = self.gan
        z_t = gan.uniform_encoder.sample
        next_frame = gan.session.run(gan.next_frame, {gan.last_frame_2: self.last_frame_2, gan.last_frame_1: self.last_frame_1})
        self.last_frame_1 = self.last_frame_2
        self.last_frame_2 = next_frame
        self.i += 1
        if self.i > 120:
            self.last_frame_1, self.last_frame_2 = gan.session.run([gan.inputs.x1, gan.inputs.x2])

            self.i = 0

        return {
            'generator': next_frame
        }


class TrainingVideoFrameSampler(BaseSampler):
    def __init__(self, gan, samples_per_row=8):
        self.z = None

        self.last_frame_1, self.last_frame_2 = gan.session.run([gan.inputs.x1, gan.inputs.x2])
        self.c = gan.c

        self.i = 0
        BaseSampler.__init__(self, gan, samples_per_row)

    def _sample(self):
        gan = self.gan
        z_t = gan.uniform_encoder.sample
        
        next_frame = gan.session.run(gan.c, {gan.last_frame_2: self.last_frame_2, gan.last_frame_1: self.last_frame_1})
        next_frame2 = gan.session.run(gan.c, {gan.last_frame_2: next_frame, gan.last_frame_1: self.last_frame_2})
        self.i += 1

        return {
            'generator': np.vstack([self.last_frame_1, self.last_frame_2, next_frame, next_frame2])
        }



def setup_gan(config, inputs, args):
    gan = AliNextFrameGAN(config, inputs=inputs)

    if(args.action != 'search' and os.path.isfile(save_file+".meta")):
        gan.load(save_file)

    tf.train.start_queue_runners(sess=gan.session)

    config_name = args.config
    GlobalViewer.title = "[hypergan] next-frame " + config_name
    GlobalViewer.enabled = args.viewer
    GlobalViewer.zoom = 1

    return gan

def train(config, inputs, args):
    gan = setup_gan(config, inputs, args)
    sampler = lookup_sampler(args.sampler or TrainingVideoFrameSampler)(gan)
    samples = 0

    #metrics = [batch_accuracy(gan.inputs.x, gan.uniform_sample), batch_diversity(gan.uniform_sample)]
    #sum_metrics = [0 for metric in metrics]
    for i in range(args.steps):
        gan.step()

        if args.action == 'train' and i % args.save_every == 0 and i > 0:
            print("saving " + save_file)
            gan.save(save_file)

        if i % args.sample_every == 0:
            sample_file="samples/%06d.png" % (samples)
            samples += 1
            sampler.sample(sample_file, args.save_samples)

        #if i > args.steps * 9.0/10:
        #    for k, metric in enumerate(gan.session.run(metrics)):
        #        print("Metric "+str(k)+" "+str(metric))
        #        sum_metrics[k] += metric 

    tf.reset_default_graph()
    return []#sum_metrics

def sample(config, inputs, args):
    gan = setup_gan(config, inputs, args)
    sampler = lookup_sampler(args.sampler or VideoFrameSampler)(gan)
    samples = 0
    for i in range(args.steps):
        sample_file="samples/%06d.png" % (samples)
        samples += 1
        sampler.sample(sample_file, args.save_samples)

def search(config, inputs, args):
    metrics = train(config, inputs, args)

    config_filename = "colorizer-"+str(uuid.uuid4())+'.json'
    hc.Selector().save(config_filename, config)
    with open(args.search_output, "a") as myfile:
        myfile.write(config_filename+","+",".join([str(x) for x in metrics])+"\n")

if args.action == 'train':
    metrics = train(config, inputs, args)
    print("Resulting metrics:", metrics)
elif args.action == 'sample':
    sample(config, inputs, args)
elif args.action == 'search':
    search(config, inputs, args)
else:
    print("Unknown action: "+args.action)