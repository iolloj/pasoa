import argparse
import logging
import datetime

import jax
import jax.numpy as np
import optax
import blackjax

from torch.utils.tensorboard import SummaryWriter

from exptax.model_sources import Sources, PremiumSources, CES
from exptax.estimators import pce_bound, reinforce_pce
from exptax.optimizers import SGD, ParallelTempering
from exptax.run_utils import (
    SMC,
)
from exptax.inference.CES_smc import SMC_CES


def make_CSGLD(exp_model, writer, opt_steps, energy):
    # CSGLD
    delta_u = 1e-1
    levels = np.arange(0, 1.1, delta_u)
    histogram = CSGLDHistogram(levels=levels, delta_u=delta_u)

    opt = CSGLD(
        exp_model=exp_model,
        writer=writer,
        step_size=0.1,
        energy=energy,
        histogram=histogram,
        mini_batch_size=100,
        opt_steps=opt_steps,
        temp=1.0,
    )
    return opt


def make_SGD(exp_model, writer, opt_steps, energy):
    # SGD
    exponential_decay_scheduler = optax.exponential_decay(
        init_value=1e-1,
        transition_steps=opt_steps,
        decay_rate=0.98,
        transition_begin=int(opt_steps * 0.25),
        staircase=False,
    )

    opt = SGD(
        exp_model,
        writer,
        opt_steps,
        num_meas,
        {"learning_rate": exponential_decay_scheduler},
        optax.adam,
        energy,
    )

    return opt


def make_PT(exp_model, writer, opt_steps, energy):
    # PT
    temps = np.array([0.001, 0.01, 0.03, 0.05, 0.09])
    # temps = np.array([0.01, 0.3, 0.5, 70., 80., 100., 1000.])
    step_size = 1e1 * (temps) ** (1 / 4)

    opt = ParallelTempering(
        exp_model,
        writer,
        temps,
        blackjax.additive_step_random_walk.normal_random_walk,
        {"sigma": step_size},
        # blackjax.mala,
        # {"step_size":step_size},
        # hmc_parameters,
        opt_steps,
        energy,
    )

    return opt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SMC experiment design")
    parser.add_argument("--inner_samples", default=1000, type=int)
    parser.add_argument("--outer_samples", default=1000, type=int)
    parser.add_argument("--type_loss", default="PCE", type=str)
    parser.add_argument("--opt_type", default="SGD", type=str)
    parser.add_argument("--exp_type", default="sources", type=str)
    parser.add_argument("--num_sources", default=2, type=int)
    parser.add_argument("--name", default="", type=str)
    parser.add_argument("--iter_per_meas", default=1000, type=int)
    parser.add_argument("--num_meas", default=30, type=int)
    parser.add_argument("--plot_meas", action=argparse.BooleanOptionalAction)
    parser.add_argument("--no_temp", action=argparse.BooleanOptionalAction)
    parser.add_argument("--profile", action=argparse.BooleanOptionalAction)
    parser.add_argument("--prefix", default="", type=str)
    parser.add_argument("--plot_post", action=argparse.BooleanOptionalAction)
    parser.add_argument("--log_SGD", action=argparse.BooleanOptionalAction)
    parser.add_argument("--plot_hist", action=argparse.BooleanOptionalAction)
    parser.add_argument("--mini_batch", default=None, type=int)
    parser.add_argument("--rng_key", default=1, type=int)
    parser.add_argument(
        "--logging",
        default="warning",
        help="Provide logging level. Example --loglevel debug, default=warning",
    )
    args = parser.parse_args()

    logging.basicConfig(level=args.logging.upper())
    logging.info("Logging now setup.")

    dir_name = "runs/" + args.prefix + args.exp_type + "/" + args.name + "/"
    tensorboard_name = (
        dir_name
        + args.name
        + datetime.datetime.now().strftime("%S_%H:%M_%d_%m")
        + f"_{args.rng_key}_inner_{args.inner_samples}_outer_{args.outer_samples}"
    )
    print(dir_name)
    writer = SummaryWriter(tensorboard_name)
    writer.add_text("Params", str(args)[10:-1])

    rng_key = jax.random.PRNGKey(args.rng_key)
    inference_method = SMC
    if args.exp_type == "sources":
        experiment_model = Sources(
            max_signal=1e-4,
            base_signal=0.1,
            num_sources=args.num_sources,
            rng_key=rng_key,
            noise_var=0.5,
        )
        loss = pce_bound

    elif args.exp_type == "premium":
        experiment_model = PremiumSources(
            num_sources=2,
            rng_key=rng_key,
        )
        loss = pce_bound

    elif args.exp_type == "ces":
        experiment_model = CES(rng_key=rng_key)
        inference_method = SMC_CES
        loss = reinforce_pce
        # loss = pce_bound

    logging.info(experiment_model.ground_truth)

    opt_steps = args.iter_per_meas
    total_steps = opt_steps * args.num_meas
