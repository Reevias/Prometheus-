# Machine learning and LLM path

The owner starred a set of repositories that together make a clear path from zero to building language models. Use this order, and adjust the pace to the owner's level.

## Stage order

1. **Python and maths basics.** Python, NumPy, linear algebra, probability and a little calculus. Free books from free-programming-books (Python and mathematics sections).
2. **Classic machine learning from scratch.** ML-From-Scratch (github.com/eriklindernoren/ML-From-Scratch) implements regression, trees, clustering, boosting and small neural networks in plain NumPy, so the owner sees what libraries hide.
3. **Deep learning with PyTorch.** PyTorch-Tutorial (github.com/MorvanZhou/PyTorch-Tutorial) gives short, runnable examples: regression, classification, CNNs, RNNs, autoencoders. Stanford TensorFlow tutorials (github.com/chiphuyen/stanford-tensorflow-tutorials) cover the same ideas in TensorFlow if the owner wants it.
4. **Generative models.** PyTorch-GAN (github.com/eriklindernoren/PyTorch-GAN) has many GAN variants in PyTorch. Treat it as reading and small experiments, not a full stage.
5. **LLMs from scratch.** LLMs-from-scratch (github.com/rasbt/LLMs-from-scratch), the code for Sebastian Raschka's book: tokenising text, attention, building a GPT-style model, pretraining, then fine-tuning for classification and for following instructions. This is the core stage for the owner's interest in AI brains.
6. **Shipping ML.** Made-With-ML (github.com/GokuMohandas/Made-With-ML) covers design, data, training, evaluation, serving, testing and CI for ML systems.
7. **Testing AI safety.** garak (github.com/NVIDIA/garak) probes language models for prompt injection, jailbreaks and data leaks. Good final project: run it against a local model and read the report.

## Notes for this laptop

The owner's laptop has 8 GB of RAM and no GPU. Stages 1 to 3 run fine on it. For stage 5, the small GPT in LLMs-from-scratch trains slowly on CPU; suggest the smallest settings, or a free notebook service with a GPU for longer runs.
